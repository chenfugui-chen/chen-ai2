"""
向量知识库检索工具（RAG 的「检索」半边，切片级）

和 search_profile 的分工：
- search_profile / read_document → 个人资料库（profile/ 目录）
    小规模、按类别组织的资料文件，靠「文件名 + LLM 生成的一句话描述 + 类别关键词」定位，读全文。
- search_knowledge              → 向量知识库（chroma_db/ 目录）
    由 import_kb.py 导入的任意文档，先切片再向量化，靠语义相似度召回 Top-K 切片。

具体走哪条路由，由 Agent（DeepSeek 原生 function calling）自己判断，
判断规则写在 agent/prompts.py 的【工具选择规则】里。
"""

import math

from langchain_core.tools import tool

import config_data as config
from vector_stores import get_vector_store, is_empty

# 空库时的固定提示：不触发模型下载，也告诉 Agent 下一步该干什么
_EMPTY_HINT = (
    "向量知识库当前为空（chroma_db 里没有任何切片）。\n"
    "如果这个问题确实需要知识库文档，请如实告知用户「知识库还没有导入资料」，"
    "并提示他可以运行：python import_kb.py <文件或目录> 把文档导入知识库。\n"
    "涉及提问者本人的问题请改用 search_profile 查询个人资料库。"
)


def _search_with_relevance(query: str, k: int) -> list[tuple]:
    """
    检索并换算出 0~1 的相关度（越大越相关）。

    Chroma 默认用 L2 距离，而本项目的嵌入向量都是归一化的，所以
        distance = sqrt(2 - 2*cos)  落在 [0, 2]
    用 1 - distance/sqrt(2) 换算成相关度，再截断到 [0,1]。

    不用 langchain 的 similarity_search_with_relevance_scores：它遇到负分
    （完全不相关的切片）会刷 UserWarning，而截断后显示 0.00 更直观。
    实测：真正相关的切片约 0.42~0.75，无关切片 0~0.2。
    """
    pairs = get_vector_store().similarity_search_with_score(query, k=k)
    results = []
    for doc, distance in pairs:
        score = 1.0 - (float(distance) / math.sqrt(2.0))
        results.append((doc, max(0.0, min(1.0, score))))
    return results


def _format_chunks(docs: list) -> str:
    """把召回切片拼成给 Agent 的文本块（统一用 【资料：文件名】 标记，来源才能被程序抓到）"""
    blocks = []
    for doc, score in docs:
        source = doc.metadata.get("source", "未知来源")
        create_time = doc.metadata.get("create_time", "")
        content = doc.page_content or ""

        limit = config.vector_content_max_chars
        if len(content) > limit:
            content = content[:limit] + f"\n……（切片过长，已截断，原切片约 {len(content)} 字）"

        head = f"【资料：{source}】\n来源：向量知识库"
        if create_time:
            head += f"（导入时间 {create_time}）"
        if score is not None:
            head += f"\n相关度：{score:.2f}（0~1，越高越相关）"
        blocks.append(f"{head}\n内容：\n{content}")

    return "\n\n----------\n\n".join(blocks)


@tool
def search_knowledge(query: str, top_k: int = 4) -> str:
    """在「向量知识库」中做语义检索，返回与问题最相关的文档切片原文。

    向量知识库（chroma_db/）里放的是用户自己用 import_kb.py 导入的文档，
    例如公司资料、产品手册、制度流程、行业知识、面试题库等，
    与「个人资料库」(profile/) 是两套互相独立的数据。

    适用场景：与提问者本人无关的文档知识类问题（概念解释、业务流程、产品/制度内容、
    行业知识等）。凡是涉及「我本人」的事实（学历、经历、技能、项目、求职意向），
    必须改用 search_profile，不要用本工具。

    Args:
        query: 检索问题或关键词，例如 "差旅报销标准是多少"
        top_k: 返回几个最相关的切片，默认 4
    """
    try:
        limit = int(top_k)
    except (TypeError, ValueError):
        limit = config.vector_top_k
    limit = max(1, min(limit, config.vector_max_top_k))

    # 空库直接返回提示：不加载嵌入模型（否则空库也要先下载几十 MB 模型）
    if is_empty():
        return _EMPTY_HINT

    try:
        docs = _search_with_relevance(query, limit)
    except Exception as e:
        return (
            f"向量知识库检索失败：{e}\n"
            f"（常见原因：首次使用需要联网下载本地嵌入模型，缓存目录：{config.embedding_cache_dir}；"
            f"或知识库文件被破坏，可重新运行 python import_kb.py 导入）"
        )

    # 相关度过滤：阈值 0.0 表示不启用（只把分数标注给大模型看，由它判断够不够相关）；
    # 想强制丢弃无关切片，就把 config.vector_min_relevance 调到 0.2~0.35
    if config.vector_min_relevance > 0:
        docs = [(doc, score) for doc, score in docs
                if score is None or score >= config.vector_min_relevance]

    if not docs:
        return f"向量知识库中没有检索到与「{query}」相关的内容。"

    return _format_chunks(docs)


if __name__ == "__main__":
    print(search_knowledge.invoke({"query": "差旅报销标准"}))
