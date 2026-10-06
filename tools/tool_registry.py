"""
工具注册中心

所有工具（个人资料检索 / 文档读取 / 向量知识库检索，未来还会有更多）
统一在这里登记，Agent 只从这里取工具，不硬编码具体有哪些工具。

新增工具只需要两步：
    1. 在 tools/ 下新写一个函数，用 langchain 的 @tool 装饰
    2. 在本文件末尾 register_tool(your_tool)
Agent 的核心逻辑完全不用改。

注意：工具之间的「该用哪一个」由大模型自己判断，
对应的判断规则集中在 agent/prompts.py，不要散落到这里。
"""

from langchain_core.tools import BaseTool

# 工具登记表：name -> 工具对象
_TOOLS: dict[str, BaseTool] = {}


def register_tool(tool: BaseTool) -> None:
    """注册一个工具（同名会覆盖，重复导入不会报错）"""
    _TOOLS[tool.name] = tool


def list_tools() -> list[BaseTool]:
    """返回当前已注册的全部工具，供 Agent 绑定给大模型"""
    return list(_TOOLS.values())


def get_tool_map() -> dict[str, BaseTool]:
    """返回 name -> 工具对象 的映射，供 Agent 按名字执行工具调用"""
    return dict(_TOOLS)


def get_tool(name: str) -> BaseTool | None:
    """按名字取一个工具"""
    return _TOOLS.get(name)


def get_tool_names() -> list[str]:
    """返回当前已注册的工具名列表"""
    return list(_TOOLS.keys())


# ===== 注册内置工具（新增工具时在下面追加一行即可） =====
from tools.document_reader_tool import read_document
from tools.knowledge_search_tool import search_knowledge
from tools.memory_tools import recall, remember, search_history
from tools.profile_search_tool import search_profile

register_tool(search_profile)
register_tool(read_document)
register_tool(search_knowledge)
register_tool(remember)
register_tool(recall)
register_tool(search_history)
