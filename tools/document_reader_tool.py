"""
文档读取工具

把「按路径/文件名把一份资料读成纯文本」这件事封装成一个 Agent 可调用的工具。
Agent 不需要知道文件是 PDF 还是 DOCX，底层解析全部交给 document.parsers。
"""

import os

from langchain_core.tools import tool

import config_data as config
from document.parsers import read_document as _parse_document
from index.profile_index import get_index

# 安全边界：Agent 只允许读取个人资料目录下的文件
_PROFILE_ROOT = os.path.abspath(config.profile_directory)


def _resolve_path(file_path: str) -> str:
    """
    把 Agent 传来的「路径」或「文件名」统一解析成资料库内的真实绝对路径。
    支持三种写法：绝对路径、相对项目根目录的路径、纯文件名。
    """
    candidate = os.path.abspath(file_path)
    if os.path.isfile(candidate):
        if not candidate.startswith(_PROFILE_ROOT):
            raise ValueError(f"出于安全限制，只允许读取个人资料目录内的文件：{config.profile_directory}")
        return candidate

    # 退化成按文件名在索引里找（大模型经常只传文件名）
    target_name = os.path.basename(file_path)
    matches = [e for e in get_index() if e["file"] == target_name]
    if len(matches) == 1:
        return matches[0]["path"]
    if len(matches) > 1:
        # 同名文件出现在多个类别目录下，让 Agent 自己补全路径再来一次
        paths = "、".join(e["rel_path"] for e in matches)
        raise ValueError(f"存在多个同名文件，请用完整路径重试：{paths}")

    available = "、".join(e["rel_path"] for e in get_index()) or "（资料库为空）"
    raise ValueError(f"资料库中找不到文件「{file_path}」。当前可用资料：{available}")


@tool
def read_document(file_path: str) -> str:
    """读取个人资料库中某个文件的完整内容。

    支持格式：pdf / docx / doc / txt / md / csv / xls / xlsx。
    适用场景：已经明确知道要读哪个文件时使用。
    如果不确定该读哪个文件，请先用 search_profile 按问题定位相关文件。

    Args:
        file_path: 资料文件路径或文件名，例如 "profile/skills/技术能力.pdf" 或 "技术能力.pdf"
    """
    try:
        real_path = _resolve_path(file_path)
        content = _parse_document(real_path)
    except Exception as e:
        return f"读取失败：{e}"

    limit = config.profile_content_max_chars
    if len(content) > limit:
        content = content[:limit] + f"\n……（内容过长，已截断，全文约 {len(content)} 字）"

    return f"【资料：{os.path.basename(real_path)}】\n类别：{_category_of(real_path)}\n内容：\n{content}"


def _category_of(abs_path: str) -> str:
    """根据文件绝对路径反查所属类别"""
    for entry in get_index():
        if entry["path"] == abs_path:
            return entry["category"]
    return "other"


if __name__ == "__main__":
    print(read_document.invoke({"file_path": "技术能力.pdf"}))
