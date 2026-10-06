"""工具层：Agent 可以调用的能力都放在这里，每个工具一个文件

导入本包即完成内置工具的注册（注册逻辑在 tool_registry.py）。
"""

from tools.tool_registry import (
    get_tool,
    get_tool_map,
    get_tool_names,
    list_tools,
    register_tool,
)

__all__ = [
    "register_tool",
    "list_tools",
    "get_tool_map",
    "get_tool",
    "get_tool_names",
]
