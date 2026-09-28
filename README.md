# AI 知识库问答系统（RAG 检索增强生成）

基于 RAG（Retrieval-Augmented Generation）的知识库问答系统：把文档导入向量库，提问时自动检索相关资料并交给大模型生成回答，实现"开卷问答"，有效降低模型幻觉。

## 功能特性

- **多格式文档解析**：支持 `txt / csv / pdf / doc / docx / xls / xlsx` 共 7 种格式（旧版 `.doc` 经本机 Word 转换解析）
- **本地 Embedding**：使用 `BAAI/bge-small-zh-v1.5` 本地向量化（ONNX 推理，CPU 即可运行），不依赖云端 API、不消耗额度、可离线
- **MD5 去重**：按文本内容哈希判重，避免重复入库
- **批量导入**：支持指定文件夹递归批量导入
- **RAG 问答链路**：LangChain 构建"向量检索 → Prompt 组装 → LLM 生成"，Chroma HNSW 索引召回 Top-K 相关片段
- **多轮对话**：MySQL 存储会话与消息，历史对话注入 Prompt，实现跨轮次语义指代理解

## 项目结构

```
aiRAG/
├── app_web.py           # Streamlit 前端入口
├── config_data.py       # 全局配置（读取 .env）
├── knowledge_base.py    # 文档解析、切块、入库、去重
├── vector_stores.py     # Chroma 向量库与检索器
├── rag.py               # RAG 问答链路（检索 + DeepSeek 生成）
├── history_store.py     # MySQL 会话/消息存储
├── requirements.txt     # 依赖清单
└── .env.example         # 环境变量模板
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入自己的配置：

```bash
copy .env.example .env      # Windows
```

`.env` 内容说明：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 大模型 API Key |
| `DEEPSEEK_BASE_URL` | API 地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名，默认 `deepseek-chat` |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址与端口 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | MySQL 账号密码 |
| `MYSQL_DB` | 数据库名，默认 `ai_rag` |

> `.env` 已被 `.gitignore` 忽略，不会提交到仓库。

### 3. 初始化 MySQL

创建数据库与两张表（会话 / 消息）：

```sql
CREATE DATABASE IF NOT EXISTS ai_rag DEFAULT CHARSET utf8mb4;

USE ai_rag;

CREATE TABLE conversation (
    id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(255),
    created_at DATETIME,
    updated_at DATETIME
);

CREATE TABLE message (
    id INT PRIMARY KEY AUTO_INCREMENT,
    conversation_id INT,
    role VARCHAR(20),
    content TEXT,
    created_at DATETIME,
    FOREIGN KEY (conversation_id) REFERENCES conversation(id) ON DELETE CASCADE
);
```

### 4. 启动应用

```bash
streamlit run app_web.py
```

浏览器打开 `http://localhost:8501`，上传文档即可开始问答。

## 说明

- `chroma_db/`（向量库）与 `md5.txt`（去重记录）为运行时生成数据，未纳入版本管理。首次使用时通过页面上传文档即可自动重建。
- 本地嵌入模型首次运行会自动从 `hf-mirror.com` 下载并缓存到 `~/.cache/fastembed`，之后离线可用。
- `.doc` 解析依赖本机安装的 Microsoft Word（仅 Windows），`.xls` 使用 `xlrd` 不依赖 Office。
