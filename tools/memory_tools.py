"""
长期记忆工具（接在现有工具注册中心里，Agent 自主决定何时调用）

三个工具分工：
- remember       把关于用户的一条事实长期记住（写入 memory 表，同 key 覆盖）
- recall         回忆之前记住的事实（按 key 精确/模糊匹配）
- search_history 在历史对话记录里按关键词检索（跨所有会话）

注意：这三个工具返回的文本【不要】带「【资料：…】」标记 ——
那样会被 Agent 误当成资料来源统计，记忆不是文档资料。
"""

from langchain_core.tools import tool

import config_data as config
from memory_store import MemoryStore

_store = None  # 懒加载：第一次真正用到记忆工具时才连库 + 建表


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "……（已截断）"


@tool
def remember(key: str, value: str) -> str:
    """把一条关于用户的事实长期记住，供以后对话回忆。同一 key 会覆盖旧值。

    适用场景：用户明确让你「记住」某事，或对话中出现了值得长期保存的稳定事实
    （例如用户的偏好、姓名、所在城市、常用配置等）。不要记录一次性的临时信息。

    Args:
        key:   简短的主题词，例如 "用户姓名"、"偏好语言"、"公司名称"
        value: 要记住的具体内容，例如 "陈欣凯"
    """
    try:
        return _get_store().remember(key, value)
    except Exception as e:
        return f"记忆写入失败：{e}"


@tool
def recall(key: str = "") -> str:
    """回忆之前用 remember 记住的事实。

    适用场景：回答前需要确认「用户之前说过什么 / 有什么偏好」等长期信息时调用。
    不确定有哪些记忆时可以不传 key，工具会返回最近记住的若干条。

    Args:
        key: 要找的主题词（可省略）；省略时返回最近记住的记忆
    """
    try:
        rows = _get_store().recall(key)
    except Exception as e:
        return f"记忆读取失败：{e}"

    if not rows:
        return f"没有找到{'关于「' + key + '」的' if key else ''}任何长期记忆。"

    lines = [
        f"- {row['mem_key']}：{_truncate(str(row['mem_value']), config.memory_value_max_chars)}"
        f"（更新于 {row['updated_at']}）"
        for row in rows
    ]
    return "已记住的长期记忆：\n" + "\n".join(lines)


@tool
def search_history(query: str, limit: int = 5) -> str:
    """在历史对话记录中按关键词检索，找回「过去某轮说过的话」。

    适用场景：用户提到「我们上次聊过的…」「之前说过的…」，而当前会话上下文中
    没有相关信息时，用它跨会话检索历史消息。

    Args:
        query: 检索关键词，例如 "报销标准"、"上次说的项目"
        limit: 最多返回几条历史片段，默认 5
    """
    try:
        rows = _get_store().search_history(query, limit or config.memory_search_limit)
    except Exception as e:
        return f"历史检索失败：{e}"

    if not rows:
        return f"历史对话中没有找到包含「{query}」的内容。"

    lines = [
        f"[{row['created_at']}] {'用户' if row['role'] == 'user' else 'AI'}："
        f"{_truncate(str(row['content']), config.memory_value_max_chars)}"
        for row in rows
    ]
    return f"历史对话中与「{query}」相关的片段：\n" + "\n".join(lines)


if __name__ == "__main__":
    print(remember.invoke({"key": "用户姓名", "value": "陈欣凯"}))
    print(recall.invoke({"key": "姓名"}))
    print(search_history.invoke({"query": "项目"}))