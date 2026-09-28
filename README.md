# chen-ai2
基于 RAG 架构的外贸垂直领域智能问答系统。支持 txt、PDF、Word、Excel 等 7 种格式文档解析入库，已构建 861 个文件、15215 个文本块、约 630 万字的知识库，采用 MD5 去重与分批提交优化入库效率。检索阶段用本地中文 Embedding 结合 Chroma 向量库 HNSW 索引召回 Top-K 片段，经 LangChain 组装 Prompt 调用 DeepSeek 大模型生成回答。系统接入 MySQL 存储多轮对话历史，结合 session_state 实现会话管理，支持跨轮次语义指代理解。
