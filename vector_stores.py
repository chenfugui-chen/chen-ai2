from langchain_chroma import Chroma
import config_data as config



class VectorStoreService(object):#向量数据库服务
    def __init__(self):
        self.embedding = config.get_embedding_function()#本地嵌入模型（与入库时保持同一模型）

        self.vector_store = Chroma(
            collection_name = config.collection_name,#向量数据库集合名称
            embedding_function = self.embedding,#嵌入模型
            persist_directory = config.persist_directory,#数据库路径
            
        )
    def get_retriever(self):#获取检索器
        return self.vector_store.as_retriever(search_kwargs={"k":config.similarity_threshold})#返回检索器，每次检索匹配的文档数量

if __name__ == "__main__":
    
    retriever = VectorStoreService().get_retriever()
    res = retriever.invoke("我的体重130斤,尺码推荐")
    print(res)