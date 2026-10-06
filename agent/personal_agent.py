"""
Main Agent：个人 AI 助手的统一入口

一次问答的完整流程：
    用户问题
      → 组装上下文（系统提示词 + 历史对话 + 当前问题）
      → DeepSeek 判断该走哪条检索路径（原生 function calling）：
            · 涉及本人的问题   → search_profile / read_document（个人资料库，读全文）
            · 文档知识类问题   → search_knowledge（向量知识库，召回切片）
            · 两者都相关       → 两个工具都调，综合后回答
      → 把工具返回的资料交回给 DeepSeek
      → 生成最终答案（并自动附上资料来源，区分「个人资料」与「知识库」）
      → 写入 MySQL 历史

新增能力的唯一方式是往工具注册中心注册工具，本文件不需要跟着改。
"""

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

import config_data as config
from agent.prompts import MATCH_SYSTEM_PROMPT, SYSTEM_PROMPT
from history_store import HistoryStore
from index.profile_index import get_index
from tools import get_tool_map, list_tools, register_tool

# 从工具返回文本里提取资料来源（工具统一用 【资料：文件名】 标记，且都位于行首）。
# 必须锚定行首：文档正文里可能恰好出现「【资料：xxx】」这样的示例文字，
# 不锚定就会把它误当成来源（曾实测到把文档里举例的「文件名」抓成来源）。
_SOURCE_PATTERN = re.compile(r"^【资料：(.+?)】", re.MULTILINE)

# 不同工具取回来的资料，在最终「来源」行里分开标注，便于看出答案依据的是哪套数据
_TOOL_SOURCE_KIND = {
    "search_knowledge": "知识库",
}
_DEFAULT_SOURCE_KIND = "个人资料"
_SOURCE_KIND_ORDER = ["个人资料", "知识库"]

# 模型有时会自己写「来源：xxx」，且可能写错（例如写成类别名而不是文件名）。
# 来源必须由程序根据本轮真实调用过的工具生成，所以先把模型写的来源行删掉。
_MODEL_SOURCE_LINE = re.compile(r"^[ \t]*(来源|资料来源|参考来源|参考资料)[ \t]*[:：].*$", re.MULTILINE)


def _format_sources(sources: list[tuple[str, str]]) -> str:
    """
    把 [(资料名, 来源类型)] 拼成一行来源说明，按类型分组，例如：
        来源：技术能力.pdf、04_FDE实习经历.docx（个人资料）；员工手册.pdf（知识库）
    """
    if not sources:
        return ""

    groups: dict[str, list[str]] = {}
    for name, kind in sources:
        groups.setdefault(kind, []).append(name)

    ordered = [k for k in _SOURCE_KIND_ORDER if k in groups]
    ordered += [k for k in groups if k not in ordered]
    return "来源：" + "；".join(f"{'、'.join(groups[k])}（{k}）" for k in ordered)


class PersonalAssistantAgent:
    """个人资料 Agent：所有对话请求的统一入口"""

    def __init__(self):
        self.history_store = HistoryStore()

        # 预热资料索引（扫描 profile/ + 补齐缺失的文件描述），避免第一次提问时卡顿
        try:
            index = get_index()
            print(f"[Agent] 资料索引就绪，共 {len(index)} 个文件")
        except Exception as e:
            print(f"[Agent] 资料索引构建失败：{e}")

        # 对话模型（stream_usage=True：流式调用时也返回 token 用量，供 usage 事件使用）
        self.model = ChatOpenAI(
            model=config.deepseek_model,
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            temperature=0.3,
            stream_usage=True,
        )

        # 工具：从注册中心取，不硬编码
        self._refresh_tools()

    # ============ 工具管理 ============

    def _refresh_tools(self) -> None:
        """从注册中心同步工具列表，并重新绑定给大模型"""
        self.tools = list_tools()
        self.tool_map = get_tool_map()
        self.model_with_tools = self.model.bind_tools(self.tools) if self.tools else self.model

    def register_tool(self, tool) -> None:
        """注册一个新工具（未来接 VectorSearchTool / GitHubTool 时调这一个方法即可）"""
        register_tool(tool)
        self._refresh_tools()

    # ============ 对外入口 ============

    def ask(self, user_input: str, conversation_id: int | None = None) -> tuple[str, int]:
        """
        处理一轮问答。
        :param user_input:      用户的当前问题
        :param conversation_id: 会话 ID；为 None 时自动新建会话
        :return:                (最终回答, 会话 ID)
        """
        if conversation_id is None:
            conversation_id = self.history_store.create_conversation(user_input)

        messages = self._build_messages(conversation_id, user_input)
        answer, sources = self._run_tool_loop(messages)

        # 来源以程序统计的为准：先清掉模型自己写的来源行，再附上真实的
        answer = _MODEL_SOURCE_LINE.sub("", answer).strip()
        source_line = _format_sources(sources)
        if source_line:
            answer = f"{answer}\n\n{source_line}"

        self.history_store.add_message(conversation_id, "user", user_input)
        self.history_store.add_message(conversation_id, "assistant", answer)
        return answer, conversation_id

    # ============ 流式入口（供 FastAPI 以 SSE 推送） ============

    def stream(self, user_input: str, conversation_id: int | None = None):
        """
        流式问答：把一轮对话拆成结构化事件依次产出，供后端以 SSE 推送。
        事件为 dict，含 event / data 两个键，顺序如下：
            meta        → {"conversation_id": int}          会话 id（新建时由后端回传）
            tool_call   → {"name", "args"}                  模型决定调用某个工具
            tool_result → {"name", "chars", "preview"}      工具返回结果摘要
            answer      → {"delta": str}                    增量文本（前端做打字机）
            usage       → {"input_tokens","output_tokens","total_tokens"}
            done        → {"conversation_id": int}
        """
        if conversation_id is None:
            conversation_id = self.history_store.create_conversation(user_input)
        yield {"event": "meta", "data": {"conversation_id": conversation_id}}

        messages = self._build_messages(conversation_id, user_input)
        sources: list[tuple[str, str]] = []
        answer_parts: list[str] = []
        usage: dict = {}

        for _ in range(config.agent_max_tool_iterations):
            aggregated = None
            # 流式调用：一边把文本增量推给前端，一边聚合出完整响应以判断是否要调工具
            for chunk in self.model_with_tools.stream(messages):
                aggregated = chunk if aggregated is None else aggregated + chunk
                self._merge_usage(usage, chunk)
                text = self._chunk_text(chunk)
                if text:
                    answer_parts.append(text)
                    yield {"event": "answer", "data": {"delta": text}}

            if aggregated is None:
                break
            messages.append(aggregated)

            if not aggregated.tool_calls:
                break

            for call in aggregated.tool_calls:
                yield {"event": "tool_call",
                       "data": {"name": call.get("name"), "args": call.get("args") or {}}}
                result = self._execute_tool(call)
                kind = _TOOL_SOURCE_KIND.get(call.get("name"), _DEFAULT_SOURCE_KIND)
                for source in _SOURCE_PATTERN.findall(result):
                    if (source, kind) not in sources:
                        sources.append((source, kind))
                yield {"event": "tool_result",
                       "data": {"name": call.get("name"), "chars": len(result), "preview": result[:160]}}
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        else:
            # 轮次用尽时模型可能仍停在「要调工具」，补一次不带工具的流式调用逼出最终答案
            for chunk in self.model.stream(messages):
                self._merge_usage(usage, chunk)
                text = self._chunk_text(chunk)
                if text:
                    answer_parts.append(text)
                    yield {"event": "answer", "data": {"delta": text}}

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "抱歉，我暂时无法回答这个问题。"
            yield {"event": "answer", "data": {"delta": answer}}

        # 来源由程序根据本轮真实工具调用生成，追加到答案末尾（流式，所以作为最后一段增量）
        source_line = _format_sources(sources)
        if source_line:
            answer = f"{answer}\n\n{source_line}"
            yield {"event": "answer", "data": {"delta": f"\n\n{source_line}"}}

        self.history_store.add_message(conversation_id, "user", user_input)
        self.history_store.add_message(conversation_id, "assistant", answer)

        if usage:
            yield {"event": "usage", "data": usage}
        yield {"event": "done", "data": {"conversation_id": conversation_id}}

    @staticmethod
    def _chunk_text(chunk) -> str:
        """从流式分片里取文本增量（兼容 str 与内容块列表两种形态）"""
        content = getattr(chunk, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "".join(parts)
        return ""

    @staticmethod
    def _merge_usage(usage: dict, chunk) -> None:
        """把分片里的 token 用量合并进 usage（通常只有最后一帧带完整用量）"""
        meta = getattr(chunk, "usage_metadata", None)
        if not meta:
            return
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if meta.get(key) is not None:
                usage[key] = meta[key]

    # ============ 岗位匹配分析（HR 粘贴岗位职责后调用） ============

    def match_job(self, job_description: str) -> str:
        """
        岗位匹配分析：HR 粘贴岗位职责后，结合本人真实资料做匹配度分析。
        使用独立的系统提示词（MATCH_SYSTEM_PROMPT），复用同一套工具与工具循环。
        """
        job_description = (job_description or "").strip()
        if not job_description:
            return "请先提供岗位职责或任职要求。"

        messages = [
            SystemMessage(content=MATCH_SYSTEM_PROMPT),
            HumanMessage(content=f"以下是岗位职责与任职要求，请分析我与该岗位的匹配度：\n\n{job_description}"),
        ]

        response = None
        for _ in range(config.agent_max_tool_iterations):
            response = self.model_with_tools.invoke(messages)
            messages.append(response)
            if not response.tool_calls:
                break
            for call in response.tool_calls:
                result = self._execute_tool(call)
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        else:
            # 轮次用尽仍在调工具，补一次不带工具的调用逼出分析结论
            response = self.model.invoke(messages)

        return self._content_to_text(response.content if response else "")

    @staticmethod
    def _content_to_text(content) -> str:
        """把模型返回内容统一成纯文本（兼容 str 与内容块列表两种形态）"""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", ""))
            return "".join(parts).strip()
        return ""

    # ============ 内部实现 ============

    def _build_messages(self, conversation_id: int, user_input: str) -> list:
        """系统提示词 + 历史对话 + 当前问题"""
        messages = [SystemMessage(content=SYSTEM_PROMPT)]

        history = self.history_store.get_history_messages(
            conversation_id, limit=config.history_max_messages
        )
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

        messages.append(HumanMessage(content=user_input))
        return messages

    def _run_tool_loop(self, messages: list) -> tuple[str, list[tuple[str, str]]]:
        """
        工具调用循环：让模型自己决定调哪个工具、调几次（最多 agent_max_tool_iterations 轮）
        :return: (最终答案, [(资料名, 来源类型)]，来源类型用于区分个人资料 / 知识库)
        """
        sources: list[tuple[str, str]] = []
        response = None

        for _ in range(config.agent_max_tool_iterations):
            response = self.model_with_tools.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break

            for call in response.tool_calls:
                result = self._execute_tool(call)
                kind = _TOOL_SOURCE_KIND.get(call.get("name"), _DEFAULT_SOURCE_KIND)
                for source in _SOURCE_PATTERN.findall(result):
                    if (source, kind) not in sources:
                        sources.append((source, kind))
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

        # 轮次用尽时模型可能还停在「要调工具」的状态，再补一次不带工具的调用把它逼出最终答案
        if response is not None and response.tool_calls:
            response = self.model.invoke(messages)

        answer = self._to_text(response)
        if not answer:
            answer = "抱歉，我暂时无法回答这个问题。"
        return answer, sources

    def _execute_tool(self, call: dict) -> str:
        """执行一次工具调用，任何异常都转成文本返回给模型，避免整轮对话崩掉"""
        name = call.get("name")
        tool = self.tool_map.get(name)
        if tool is None:
            return f"工具调用失败：不存在名为「{name}」的工具。可用工具：{'、'.join(self.tool_map)}"
        try:
            return str(tool.invoke(call.get("args") or {}))
        except Exception as e:
            return f"工具「{name}」执行失败：{e}"

    @staticmethod
    def _to_text(response) -> str:
        """把模型返回的内容统一转成字符串"""
        if response is None:
            return ""
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        return str(content).strip()


if __name__ == "__main__":
    agent = PersonalAssistantAgent()
    print(f"已注册工具：{', '.join(agent.tool_map)}")

    conv_id = None
    questions = [
        
        "介绍一下你的 FDE 实习经历。",
        "你做过哪些 AI 项目？",
        "你会哪些技术？",
        "你也可以把岗位职责输入进来，我会根据陈欣凯的经历回答你他是否和这个岗位职责相关。",
    ]
    for q in questions:
        print("\n" + "=" * 60)
        print(f"用户：{q}")
        answer, conv_id = agent.ask(q, conv_id)
        print(f"Agent：{answer}")
    print(f"\n当前会话 ID：{conv_id}")
