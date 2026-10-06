"""
个人资料检索工具（不使用向量数据库）

流程：用户问题 → 按类别 / 关键词在轻量索引里定位候选文件 → 只读相关文件 → 返回原文摘录

这样 Agent 一次只拿到「和问题相关」的资料，而不是把整个资料库塞进提示词。
将来资料规模变大时，可以再注册一个 VectorSearchTool，让 Agent 自己选择用哪个。
"""

from langchain_core.tools import tool

import config_data as config
from document.parsers import read_document
from index.profile_index import get_index

# 类别 -> 中文关键词提示（数据而非逻辑：新增类别时在这里补一组词即可，
# 帮助「auto 模式」在没有显式传 category 时也能定位到正确类别）
_CATEGORY_HINTS = {
    # 身份类问题（你是谁 / 介绍一下你自己）应该落到基本信息，
    # 所以把「自我介绍 / 学历 / 学校」这类词放在 basic_info，而不是 self_introduction
    "basic_info": ["基本信息", "个人信息", "姓名", "年龄", "身高", "联系方式", "电话", "邮箱", "出生",
                   "学历", "学校", "专业", "毕业", "你是谁", "介绍自己", "自我介绍", "个人简介", "自我评价"],
    # self_introduction 目录现在放的是「个人 AI 助手」项目介绍，关键词按项目来配
    "self_introduction": ["项目", "作品", "airag", "助手", "系统", "架构", "技术栈", "做过", "开发"],
    "fde_experience": ["实习", "经历", "工作", "公司", "岗位职责", "在职"],
    "skills": ["技术", "技能", "会什么", "掌握", "能力", "技术栈", "工具"],
}

# auto 模式下的最低匹配分：低于它就不硬凑答案，宁可让 Agent 如实回答「资料库中没有」
_MIN_SCORE = 0.1

# top_k 的兜底上限，避免一次读太多文件把上下文撑爆
_MAX_TOP_K = 3


def _bigrams(text: str) -> set[str]:
    """把文本切成相邻两字的集合，中文不需要分词也能做粗粒度相似度匹配"""
    text = text.lower()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _score(query: str, entry: dict) -> float:
    """
    打分 = 问题里有多少比例的「字对」能在该文件的索引信息里找到
    （索引信息 = 文件名 + LLM 生成的描述 + 类别名 + 类别关键词提示）
    """
    query_bigrams = _bigrams(query)
    if not query_bigrams:
        return 0.0

    haystack = " ".join([
        entry["file"],
        entry["description"],
        entry["category"],
        " ".join(_CATEGORY_HINTS.get(entry["category"], [])),
    ])

    hit = len(query_bigrams & _bigrams(haystack))
    score = hit / len(query_bigrams)

    # 类别名直接出现在问题里（例如问题里带 "fde"）额外加权
    if entry["category"].lower() in query.lower():
        score += 1.0
    return score


def _format_entries(entries: list[dict]) -> str:
    """把选中的文件读成文本，拼成返回给 Agent 的资料块"""
    blocks = []
    for entry in entries:
        try:
            content = read_document(entry["path"])
        except Exception as e:
            blocks.append(f"【资料：{entry['file']}】\n类别：{entry['category']}\n内容：读取失败（{e}）")
            continue

        limit = config.profile_content_max_chars
        if len(content) > limit:
            content = content[:limit] + f"\n……（内容过长，已截断，全文约 {len(content)} 字）"

        blocks.append(f"【资料：{entry['file']}】\n类别：{entry['category']}\n内容：\n{content}")
    return "\n\n----------\n\n".join(blocks)


@tool
def search_profile(query: str, category: str = "auto", top_k: int = 2) -> str:
    """在个人资料库中检索与用户问题相关的资料，并返回这些资料的原文摘录。

    适用场景：任何涉及「我本人」的问题（我是谁 / 我的专业 / 我的实习经历 / 我做过哪些项目 /
    我会哪些技术 / 我的求职意向 等），都应该先调用本工具获取资料，再基于资料回答。

    Args:
        query: 用户的问题或检索关键词，例如 "FDE实习主要做了什么？"
        category: 资料类别，可选值为 basic_info / self_introduction / fde_experience / skills；
            不确定时填 "auto"，工具会自动匹配
        top_k: 最多返回几份资料，默认 2
    """
    entries = get_index()
    if not entries:
        return "个人资料库当前为空，没有任何资料文件。"

    limit = max(1, min(top_k, _MAX_TOP_K))  # 兜底：至少 1 份，最多 _MAX_TOP_K 份

    # 第一步：定位候选文件
    if category and category != "auto":
        candidates = [e for e in entries if e["category"] == category]
        if not candidates:
            available = "、".join(sorted({e["category"] for e in entries}))
            return (f"资料库中没有「{category}」这个类别的资料。"
                    f"当前有资料的类别：{available}。可以改用 category=\"auto\" 重新检索。")
        selected = candidates[:limit]
    else:
        ranked = sorted(entries, key=lambda e: _score(query, e), reverse=True)
        if _score(query, ranked[0]) < _MIN_SCORE:
            return f"个人资料库中没有找到与「{query}」相关的内容。"
        selected = [e for e in ranked[:limit] if _score(query, e) >= _MIN_SCORE]

    # 第二步：把选中的文件读出来返回
    return _format_entries(selected)


if __name__ == "__main__":
    print(search_profile.invoke({"query": "FDE实习主要做了什么？"}))
