import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件（存放 API Key、数据库密码等敏感信息）
# 该文件已被 .gitignore 忽略，不入库；部署时请参考 .env.example 自行创建
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

md5_path = "./md5.txt"#md5值的文件路径

#向量数据库的配置 ChromaDB
collection_name = "rag"#向量数据库的集合名
persist_directory = "./chroma_db"#向量数据库的存储路径
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

# 历史会话配置（敏感信息从 .env 读取，未配置时使用默认值）
mysql_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
mysql_port = int(os.environ.get("MYSQL_PORT", "3306"))
mysql_user = os.environ.get("MYSQL_USER", "root")
mysql_password = os.environ.get("MYSQL_PASSWORD", "")   # MySQL密码
mysql_db = os.environ.get("MYSQL_DB", "ai_rag")

# DeepSeek 大模型配置（敏感信息从 .env 读取）
deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def get_embedding_function():
    """创建本地嵌入模型实例（供知识库入库和检索共用，保证两边模型一致）"""
    # 关闭 HF 的 xet 加速后端：它需要鉴权，国内会报 401 导致模型下载失败
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # 国内直连 huggingface.co 常失败，默认走镜像（首次下载模型时需要）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    return FastEmbedEmbeddings(
        model_name=embedding_model_name,
        cache_dir=embedding_cache_dir,
    )

