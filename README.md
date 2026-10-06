# 个人 AI 助手（aiRAG）

基于 **RAG + 大模型原生 Function Calling** 的个人 AI 助手，同时是一个可在线访问的「数字分身」：
由 DeepSeek 自主调度 6 个工具，在**个人资料库**与**向量知识库**之间自主路由检索证据，以第一人称流式作答，
并附上由程序生成的真实来源。前后端分离（Streamlit + FastAPI/SSE），具备跨会话长期记忆，已用 Docker Compose + Nginx 部署上线。

设计原则：**双检索路径、AI 自主路由、开卷问答（涉及本人的事实一律以工具返回的资料为准）、来源可追溯**。

## 功能特性

- **双检索路径 + AI 自主路由**：不写死 `if-else`，路由规则写进系统提示词（`agent/prompts.py`），由模型每轮自己判断走哪条路；两者都相关就都调再综合
- **工具注册中心**：6 个工具统一登记在 `tools/tool_registry.py`，新增能力 = 一个 `@tool` 函数 + 一行注册，Agent 主逻辑不改
- **SSE 流式输出**：`meta → tool_call → tool_result → answer（打字机增量）→ usage → done`，异常统一转 `error` 事件，不断连、不返 500；回答完成后工具调用与 token 用量收进可折叠的「执行链路」面板
- **来源可追溯、防幻觉**：工具返回统一带「【资料：文件名】」标记，程序用锚定行首的正则提取本轮真实来源，并先删除模型自己写的来源行（模型会写错甚至编造）
- **岗位匹配分析**：`POST /match` + 专用提示词复用同一套工具循环，输出「总体匹配度 → 相符点 → 可快速上手 → 推荐语」，严禁编造经历
- **长期记忆**：`remember` / `recall`（MySQL `memory` 表，同 key 覆盖）+ `search_history`（跨会话检索历史消息）
- **越界拒答**：与本人和知识库都无关的问题，先检索确认库里没有，再礼貌拒答并引导换问法，禁止用通用常识硬答
- **多格式文档解析**：`.txt / .md / .csv / .pdf / .doc / .docx / .xls / .xlsx` 共 8 种（旧版 `.doc` 经本机 Word 转换解析）
- **本地 Embedding**：`BAAI/bge-small-zh-v1.5`，fastembed + onnxruntime 纯 CPU 推理，不耗 API 额度、可离线；懒加载代理保证空库状态检查不触发 90MB 模型加载
- **MD5 去重 + 批量导入**：按文本内容哈希判重，`import_kb.py` 支持文件与目录递归导入
- **容器化部署**：同容器双进程（uvicorn + streamlit），MySQL 带 healthcheck，Nginx 做唯一公网入口（Basic Auth + WebSocket 透传）

## 系统架构

```
浏览器 ──HTTP/WS──> Nginx(:80, Basic Auth) ──> Streamlit(:8501, 仅界面与事件渲染)
                                                     │  POST /chat/stream (SSE)
                                                     ▼
                                            FastAPI / uvicorn(127.0.0.1:8000, 仅容器内可见)
                                                     │
                                                     ▼
                                     PersonalAssistantAgent（工具调用循环，最多 3 轮）
                                       │            │             │            │
                              profile/ 个人库   chroma_db/ 向量库  MySQL(会话/记忆)  DeepSeek API
```

一轮对话的数据流：系统提示词 + 最近 10 条历史 + 当前问题 → 流式调用模型（边推 `answer` 增量、边聚合完整响应判断是否调工具）
→ 有 `tool_calls` 就执行并以 `ToolMessage` 回灌，最多 3 轮，轮次用尽时补一次无工具调用逼出答案
→ 程序提取真实来源、删除模型自写的来源行后追加 → 问答写入 MySQL → 推 `usage` 与 `done`。

## 项目结构

```
aiRAG/
├── app_web.py                   # Streamlit 前端（对话、历史会话、岗位匹配、执行链路面板）
├── api_server.py                # FastAPI 后端（SSE 对话 + 岗位匹配 + 会话增删查）
├── config_data.py               # 全局配置（路径、切片、检索、记忆、模型、MySQL；含 HF 镜像设置）
├── import_kb.py                 # 【命令行】把文件/目录导入向量知识库
├── knowledge_base.py            # 解析、切块、入库、MD5 去重
├── vector_stores.py             # Chroma 向量库（懒加载嵌入、切片计数、来源列表）
├── history_store.py             # MySQL 会话/消息存储（contextmanager 显式关闭连接）
├── memory_store.py              # MySQL 长期记忆表（幂等建表、按 key 覆盖）
├── agent/
│   ├── personal_agent.py        # Main Agent：流式工具调用循环 + 来源标注 + 岗位匹配
│   └── prompts.py               # 系统提示词（含【工具选择规则】，路由逻辑集中在这里）
├── tools/
│   ├── tool_registry.py         # 工具注册中心（6 个工具在此登记）
│   ├── profile_search_tool.py   # search_profile：个人资料库检索（返回原文）
│   ├── document_reader_tool.py  # read_document：按路径读全文，限定 profile/ 目录，越界拒绝
│   ├── knowledge_search_tool.py # search_knowledge：向量知识库语义召回 Top-K + 相关度分数
│   └── memory_tools.py          # remember / recall / search_history
├── index/profile_index.py       # 个人资料轻量索引（LLM 生成描述，缓存到 profile/_meta.json）
├── document/parsers.py          # 8 种格式 → 纯文本
├── profile/                     # 个人资料（basic_info / self_introduction / fde_experience / skills）
├── docker/mysql/init.sql        # MySQL 首次启动自动建表（conversation / message / memory）
├── deploy/nginx/airag.conf      # 宿主机 Nginx 反向代理配置（Basic Auth + WebSocket）
├── Dockerfile                   # 同容器双进程：uvicorn 后台 + streamlit 作 PID 1
├── docker-compose.yml           # app + mysql 编排（数据卷持久化）
├── deploy.ps1                   # 一键同步代码到服务器并重建容器
├── requirements.txt             # 依赖清单
└── .env.example                 # 环境变量模板
```

> `资料/`（待导入知识库的原始文件）、`profile/`（个人真实资料）、`chroma_db/`、`md5.txt`、`.env` 均为本地/服务器各自的运行时数据，不纳入版本管理。

## 快速开始（本地）

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
copy .env.example .env      # Windows
```

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（必填） |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-chat` |
| `MYSQL_HOST` / `MYSQL_PORT` | 默认 `127.0.0.1` / `3306`；容器内由 compose 覆盖为服务名 `mysql` |
| `MYSQL_DB` / `MYSQL_USER` / `MYSQL_PASSWORD` | 数据库名与应用账号（默认库名 `ai_rag`） |
| `MYSQL_ROOT_PASSWORD` | 仅 compose 内置 MySQL 初始化需要 |
| `API_BASE_URL` | 前端访问后端的地址，默认 `http://127.0.0.1:8000` |

> `.env` 已被 `.gitignore` 忽略；`load_dotenv` 不覆盖已存在的系统环境变量，方便部署时注入。

### 3. 准备 MySQL

用仓库自带编排最省事（会自动执行 `docker/mysql/init.sql` 建表）：

```bash
docker compose up -d mysql
```

自备 MySQL 时，手动执行 `docker/mysql/init.sql` 里的三张表（`conversation` / `message` / `memory`）即可。

### 4. 导入知识库

```bash
python import_kb.py 资料/                # 目录递归导入
python import_kb.py --list               # 查看切片总数与已导入文件
```

### 5. 启动前后端

```bash
# 后端（SSE 接口）
uvicorn api_server:app --host 127.0.0.1 --port 8000
# 前端（另开一个终端）
streamlit run app_web.py
```

浏览器打开 `http://localhost:8501`，直接提问，例如「你是谁 / 你做过哪些项目 / 介绍一下你的这个 AI 项目」。

## 6 个工具（模型自主选择）

| 工具 | 作用 | 关键约束 |
|---|---|---|
| `search_profile` | 个人资料库检索，返回资料原文 | 文件名 + LLM 描述 + 类别关键词打分；默认 `top_k=2`，单文件截断 4000 字 |
| `read_document` | 按路径读取某个资料全文 | 限定 `profile/` 目录内，越界直接拒绝 |
| `search_knowledge` | 向量知识库语义召回切片 | 默认 `top_k=4`（上限 8），附相关度分数，空库返回引导语 |
| `remember` | 写入长期记忆 | 同 key 覆盖，值上限 500 字 |
| `recall` | 读取长期记忆 | 不传 key 时返回最近若干条（上限 10） |
| `search_history` | 跨会话检索历史消息 | 默认返回 5 条片段 |

## 两条检索路径

| | 个人资料库 | 向量知识库 |
|---|---|---|
| 数据位置 | `profile/` 各分类子目录 | `chroma_db/`（Chroma 持久化目录） |
| 数据来源 | 手工整理的本人资料 | `import_kb.py` 导入的任意文档 |
| 检索方式 | 文件名 + LLM 生成的一句话描述 + 类别关键词打分 | 切片向量化，按语义相似度召回 Top-K |
| 返回内容 | 整个文件全文（截断到 4000 字） | 若干相关切片（每个截断到 2000 字，附相关度） |
| 对应工具 | `search_profile` / `read_document` | `search_knowledge` |
| 适用问题 | 涉及本人的事实 | 文档知识类问题 |

回答末尾的来源行由程序生成，并区分两类来源：

```
来源：技术能力.pdf、04_FDE实习经历.docx（个人资料）；产品介绍.pdf（知识库）
```

## 后端接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/chat/stream` | 对话主入口，SSE 推送结构化事件流 |
| POST | `/match` | 岗位匹配分析（非流式，返回 `analysis` 文本） |
| GET | `/conversations` | 会话列表 |
| GET | `/conversations/{id}/messages` | 某会话的历史消息 |
| DELETE | `/conversations/{id}` | 删除会话（消息外键级联删除） |

## 导入知识库（`import_kb.py`）

```bash
python import_kb.py 员工手册.pdf            # 单个文件
python import_kb.py "D:\资料\公司文档"       # 目录递归导入
python import_kb.py --list                  # 查看知识库现状
python import_kb.py --reset --yes           # 清空整库并重置 MD5 记录（谨慎）
```

- 网页端不提供上传入口，入库只走命令行，避免访客污染知识库
- 首次导入会联网下载本地嵌入模型（约 90 MB，缓存到 `~/.cache/fastembed`），之后离线可用
- 下载走 `hf-mirror.com` 镜像。镜像地址必须设在 `config_data.py` **文件顶部**——`huggingface_hub` 在被 import 的那一刻就把 `HF_ENDPOINT` 读进模块级常量，写进函数里不生效，表现为 `ConnectTimeout WinError 10060`
- 相同内容重复导入会被 MD5 去重跳过，0 字节文件直接跳过
- 导入后需**重启应用**，Agent 与个人资料索引缓存才会刷新
- 暂不支持按文件删除切片，更新某份资料需要 `--reset --yes` 后整体重导
- **单份资料篇幅建议控制在几千字以内**：低内存机器（如 1.6GB 且无 swap 的云主机）在向量化超长文档时，进程可能被内核 OOM Killer 静默杀掉（日志无异常，需 `dmesg -T | grep -i oom` 确认）

## 生产部署（Docker Compose + Nginx）

```bash
# 服务器上：首次部署 / 代码更新后重建
docker compose up -d --build

# 导入或更新知识库后重启应用刷新缓存
docker compose restart app
```

- `mysql` 配 healthcheck，`app` 用 `depends_on + service_healthy` 等其就绪
- 项目目录以 `.:/app` 挂载，`profile/`、`chroma_db/`、`md5.txt`、`profile/_meta.json` 等运行时数据落在宿主机，重建镜像或容器都不会丢
- 嵌入模型缓存用命名卷 `fastembed_cache` 持久化，重建镜像不必重新下载
- `app` 只把 `8501` 绑到 `127.0.0.1`，公网访问统一走宿主机 Nginx；`8000` 仅容器内可见
- Nginx 配置见 `deploy/nginx/airag.conf`：Basic Auth（`htpasswd` 生成账号）、反代到 8501、透传 `Upgrade`/`Connection` 头（Streamlit 依赖 WebSocket，否则白屏）、长超时
- 一键部署：`.\deploy.ps1`（同步代码目录与文件 → 清理 `__pycache__` → 服务器重建容器）。它**只同步代码**，服务器上的 `.env`、`资料/`、`profile/`、`chroma_db/`、`md5.txt` 保持原样，资料变更需手动 `scp` 后重新导入
- 脚本不含任何服务器地址，运行前先设置环境变量：`$env:AIRAG_SERVER = "user@your-server-ip"`（可选 `$env:AIRAG_REMOTE_DIR`、`$env:AIRAG_PUBLIC_URL`）

## 关键参数（除注明外均在 `config_data.py`）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `chunk_size` / `chunk_overlap` | 500 / 100 | 递归切分粒度与重叠 |
| `vector_top_k` / `vector_max_top_k` | 4 / 8 | 语义召回切片数与上限 |
| `vector_content_max_chars` | 2000 | 单切片回传字符上限 |
| `vector_min_relevance` | 0.0 | 相关度下限（实测相关切片约 0.42~0.75，无关 0~0.2；答非所问时可调到 0.2） |
| `profile_search_top_k` | 2 | 个人资料候选文件数 |
| `profile_content_max_chars` | 4000 | 单份资料回传字符上限 |
| `agent_max_tool_iterations` | 3 | 单轮对话最多调用工具次数 |
| `history_max_messages` | 10 | 注入提示词的历史消息条数 |
| `memory_recall_limit` / `memory_search_limit` / `memory_value_max_chars` | 10 / 5 / 500 | 长期记忆与历史检索上限 |
| `temperature` | 0.3 | 模型采样温度（定义在 `agent/personal_agent.py`） |

相关度按 `1 − distance / √2` 自行换算并截断到 `[0, 1]`（Chroma 返回的是距离，不是相似度）。

## 数据库设计

| 表 | 用途 |
|---|---|
| `conversation` | 会话元信息（标题、创建/更新时间） |
| `message` | 消息记录，外键级联删除，也是 `search_history` 的检索源 |
| `memory` | 长期记忆键值对，`mem_key` 唯一索引，同 key 覆盖 |

三张表由 `docker/mysql/init.sql` 创建；`memory` 表同时由 `MemoryStore` 幂等建表，方便非容器环境。

## 踩过的坑

- **嵌入模型下载超时**：HF 镜像必须设在最早导入的 `config_data.py` 顶部（原因见上）
- **Streamlit 控制台被 `No module named 'torchvision'` 刷屏**：热重载监视器探测已加载模块会触发 transformers 懒加载视觉子模块，在 `.streamlit/config.toml` 设 `fileWatcherType = "none"` 解决
- **流式打字机失效**：后端响应头必须带 `X-Accel-Buffering: no`，否则经过 Nginx 会被缓冲成一次性输出
- **流式下拿不到 token 用量**：必须设 `stream_usage=True`
- **MySQL 连接**：MySQL 8 的 `caching_sha2_password` 需要 `cryptography` 依赖；PyMySQL 的 `with conn:` 只管事务不关连接，改用 `contextmanager` 在 `finally` 里显式 `close()`
- **同容器双进程**：用 `sh -c` 先后台起 uvicorn，再 `exec streamlit` 作 PID 1 接管信号与日志
- **本地导入的资料云端查不到**：向量库是每台机器各自的本地数据，不进 git 也不进镜像，服务器上必须重新导入一次
- **模型调工具前先冒一句英文铺垫**：提示词里硬性禁止「调工具前输出任何文字」，否则等于把思考过程暴露给用户

## 后续规划

- 个人资料检索升级为「描述 + 正文向量」混合检索
- 知识库支持按来源文件删除切片与增量更新（当前只能整库重建）
- 构建评测集，量化回答准确率与幻觉率
- 长期记忆增加语义检索与重要性分级
- 补充工具层与解析层的单元测试、接口测试
