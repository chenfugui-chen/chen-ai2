import os

# 先加载同目录下的 .env（存在就加载，不存在则忽略），这样 README 里说的
# 「复制 .env.example 为 .env」才真正生效；也支持直接用系统环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ===== HuggingFace 镜像配置（必须放在所有会用到 HF 的库被 import 之前）=====
# huggingface_hub 在「被 import 的那一刻」就会把 HF_ENDPOINT 读进模块级常量，
# 之后再改 os.environ 已经无效（实测：写在函数内部会导致仍然去连 huggingface.co，
# 国内直连必然超时，表现为首次导入知识库时 ConnectTimeout 10060）。
# config_data 是最早被导入的业务模块，所以在这里设置最稳妥；.env 里显式写了的同名变量优先。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")               # xet 后端需要鉴权，国内会 401
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 国内镜像，直连 huggingface.co 不通

# 项目根目录（本文件所在目录）：所有本地路径都基于它，避免启动目录不同导致找不到文件
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

md5_path = os.path.join(BASE_DIR, "md5.txt")#md5值的文件路径

#向量数据库的配置 ChromaDB
collection_name = "rag"#向量数据库的集合名
persist_directory = os.path.join(BASE_DIR, "chroma_db")#向量数据库的存储路径
#文本分割器的配置
chunk_size = 500#每个文本块的字符数
chunk_overlap = 100#每个文本块之间的重叠字符数
separators = ["\n\n","\n",",",".","!","?",",","."]
#最大分割字符数
max_split_char_number = 20
#本地嵌入模型的配置（不依赖任何 API，不消耗额度）
embedding_model_name = "BAAI/bge-small-zh-v1.5"#本地中文嵌入模型
embedding_cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "fastembed")#模型缓存目录

similarity_threshold = 10#每次检索匹配的文档数量

# ===================== 向量知识库检索配置（Agent 的 search_knowledge 工具） =====================
vector_top_k = 4            # 默认召回的切片数
vector_max_top_k = 8        # 召回切片数上限（防止一次塞太多内容撑爆上下文）
vector_content_max_chars = 2000  # 单个切片回传给 Agent 的字符上限
# 相关度下限：低于它的切片直接丢弃，避免把无关内容塞给大模型。
# 默认 0.0 = 不过滤（只把相关度标注出来，交给大模型自己判断）。
# 实测（bge-small-zh-v1.5）：真正相关的切片约 0.42~0.75，无关切片 0~0.2，
# 所以如果发现知识库答非所问，调到 0.2 是个比较稳妥的起点
vector_min_relevance = 0.0

# DeepSeek 大模型配置（OpenAI 兼容接口，全部从环境变量读取，方便部署时注入）
deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")#对话模型
deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")#DeepSeek API Key（从环境变量读取，不写死在代码里）
deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")#DeepSeek 接口地址

# ===================== 个人资料 Agent 配置 =====================
# 个人资料根目录（Agent 只允许在这里面检索和读取文件）
profile_directory = os.path.join(BASE_DIR, "profile")
# 文件描述缓存：首次建索引时用 LLM 生成一句话描述并写入，之后直接读缓存
profile_meta_path = os.path.join(profile_directory, "_meta.json")
# 资料类别（与 profile/ 下的子目录名一一对应，新增类别只需建目录 + 这里加一项）
# 注：放在未登记目录里的文件会被归入兜底类别 "other"（见 index/profile_index.py）
profile_categories = [
    "basic_info",          # 个人基本信息
    "self_introduction",   # 自我介绍（含「个人 AI 助手」项目介绍）
    "fde_experience",      # FDE 实习经历
    "skills",              # 技术能力
]
profile_search_top_k = 2        # search_profile 默认返回的候选文件数
profile_content_max_chars = 4000  # 单个资料文件最多回传给 Agent 的字符数（防止撑爆上下文）
agent_max_tool_iterations = 3   # Agent 单轮对话最多调用工具的次数（防止死循环）
history_max_messages = 10       # 注入提示词的历史消息条数上限

# ===================== 长期记忆（remember / recall / search_history） =====================
memory_recall_limit = 10        # recall 无 key 时返回的记忆条数上限
memory_search_limit = 5         # search_history 默认返回的历史片段数
memory_value_max_chars = 500    # 单条记忆值回传给 Agent 的字符上限

# ===================== 后端 API（FastAPI + SSE） =====================
# 前端（Streamlit）消费 SSE 事件流的后端地址；容器内两进程同网络命名空间，用 127.0.0.1
api_base_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")

# 历史会话配置（全部从环境变量读取，部署时通过 .env 或容器环境注入）
mysql_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
mysql_port = int(os.environ.get("MYSQL_PORT", "3306"))
mysql_user = os.environ.get("MYSQL_USER", "root")
mysql_password = os.environ.get("MYSQL_PASSWORD", "123456")   # MySQL密码
mysql_db = os.environ.get("MYSQL_DB", "ai_rag")


def get_embedding_function():
    """
    创建本地嵌入模型实例（供知识库入库和检索共用，保证两边模型一致）

    注意：HF_ENDPOINT / HF_HUB_DISABLE_XET 已经在文件顶部设置好了，
    不要挪回本函数内部——那样对已经 import 过的 huggingface_hub 无效。
    首次调用会下载模型（约 90MB）到 embedding_cache_dir。
    """
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    return FastEmbedEmbeddings(
        model_name=embedding_model_name,
        cache_dir=embedding_cache_dir,
    )

