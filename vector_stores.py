"""
向量数据库服务层

两类使用场景：
1) 检索：Agent 的 search_knowledge 工具
2) 入库：import_kb.py、knowledge_base.py（KnowledgeBaseService）

【为什么要有 _LazyEmbeddingFunction】
FastEmbedEmbeddings 在「构造对象」时就会去加载/下载 ONNX 模型
（pydantic validator 里直接 new 了 fastembed.TextEmbedding），
而 bge-small-zh-v1.5 首次使用需要联网下载几十 MB。
如果连「知识库是不是空的」这种判断都要先把模型拉起来，就会出现：
空库也要等下载、下载失败整个对话直接报错。

所以这里给 Chroma 套一层懒加载代理：只有真正要把文本向量化
（add_texts / similarity_search）时才创建 FastEmbedEmbeddings；
count() / get() 这类只看元数据的操作完全不碰模型。

【相关度分数怎么理解】
search_knowledge 用 similarity_search_with_relevance_scores 取分数：
Chroma 默认用 L2 距离，langchain 把它换算成 0~1 的相关度（越大越相关）。
用 bge 这类归一化向量时，0.3 以上通常已经算比较相关；换嵌入模型后数值分布会变，
所以是否按阈值过滤由 config.vector_min_relevance 控制，默认 0.0 = 不过滤。
"""

import os

import config_data as config
from langchain_chroma import Chroma


class _LazyEmbeddingFunction(object):
    """懒加载的嵌入函数代理：第一次真正需要向量化时才创建 FastEmbedEmbeddings"""

    def __init__(self):
        self._inner = None

    def _resolve(self):
        if self._inner is None:
            self._inner = config.get_embedding_function()
        return self._inner

    def embed_documents(self, texts):
        return self._resolve().embed_documents(texts)

    def embed_query(self, text):
        return self._resolve().embed_query(text)


# 全进程共用一个代理（FastEmbed 模型只加载一次）
_LAZY_EMBEDDING = _LazyEmbeddingFunction()


def get_embedding():
    """获取嵌入函数（懒加载代理，供需要显式传入的地方使用）"""
    return _LAZY_EMBEDDING


def get_vector_store() -> Chroma:
    """获取向量库实例（构造过程不会触发模型下载）"""
    return Chroma(
        collection_name=config.collection_name,
        embedding_function=_LAZY_EMBEDDING,
        persist_directory=config.persist_directory,
    )


def get_document_count() -> int:
    """
    向量库里现在有多少个切片（不会触发模型下载）。
    库还没建起来时直接返回 0，并且不创建任何文件/目录。
    """
    if not os.path.isdir(config.persist_directory):
        return 0
    try:
        return get_vector_store()._collection.count()
    except Exception:
        # 路径存在但不是合法的 Chroma 库（例如被手动破坏）时按空库处理
        return 0


def is_empty() -> bool:
    """向量知识库是否为空"""
    return get_document_count() == 0


def list_sources() -> list[str]:
    """列出知识库里出现过的来源文件名（去重、有序）"""
    if is_empty():
        return []
    try:
        data = get_vector_store().get(include=["metadatas"])
    except Exception:
        return []
    names = []
    for meta in data.get("metadatas") or []:
        source = (meta or {}).get("source")
        if source and source not in names:
            names.append(source)
    return names


class VectorStoreService(object):  # 向量数据库服务
    def __init__(self):
        self.embedding = _LAZY_EMBEDDING  # 懒加载，构造时不下载模型
        self.vector_store = get_vector_store()

    def get_retriever(self):  # 获取检索器
        return self.vector_store.as_retriever(
            search_kwargs={"k": config.similarity_threshold}  # 每次检索匹配的文档数量
        )


if __name__ == "__main__":
    print(f"向量库切片数：{get_document_count()}")
    print(f"来源文件：{list_sources()}")
