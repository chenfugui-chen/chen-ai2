import os
import config_data as config
import hashlib
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from datetime import datetime
# 文件解析统一由 document 层提供（7 种格式的解析器都在 document/parsers.py）
from document.parsers import extract_text_from_file


def check_md5(md5_str:str):
    #检查传入的ma5字符串是否已经被处理.false表示未处理,true表示已处理
    if not os.path.exists(config.md5_path):
        #如果文件不存在,则创建一个空文件
        #创建文件
        open(config.md5_path,'w',encoding="utf-8").close()
        return False
    else:
        for line in open(config.md5_path,'r',encoding="utf-8").readlines():
            line = line.strip()#去掉换行符和空格等特殊字符
            if line == md5_str:
                return True
        return False

def save_md5(md5_str:str):
    #保存传入的ma5字符串
    with open(config.md5_path,'a',encoding="utf-8") as f:
        f.write(md5_str+"\n")
    

def get_string_md5(input_str:str,encoding='utf-8'):
    #将传入的字符串转换为md5字符串
    
    str_bytes = input_str.encode(encoding=encoding)#将传入的字符串转化为字节数组
    
    md5_obj = hashlib.md5()#创建md5对象
    
    md5_obj.update(str_bytes)#将字节数组传入md5对象
    
    md5_str = md5_obj.hexdigest()#获取md5字符串
    return md5_str


class KnowledgeBaseService(object):#知识库服务类:
    #负责知识库的上传,查询,删除等操作
    
    def __init__(self):#初始化知识库服务类
        os.makedirs(config.persist_directory,exist_ok=True)#传入向量数据库的存储路径
        self.chroma = Chroma(
            collection_name = config.collection_name,#数据库的表名
            embedding_function = config.get_embedding_function(),#本地嵌入模型（与检索保持一致）
            persist_directory = config.persist_directory,#数据库的存储路径
        )#向量存储的实例
        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size = config.chunk_size,#每个文本块的字符数
            chunk_overlap = config.chunk_overlap,#每个文本块之间的重叠字符数
            separators = config.separators,#分隔符列表
            length_function = len,#计算文本块的长度的函数
            
        ) #文本分割器的对象
        
    def upload_by_str(self,data:str,filename:str):#filename是文件名,self是知识库服务类的实例
        #把传入的字符串进行向量化,存入向量数据库
        #将字符串转化为md5值
        md5_hex = get_string_md5(data)
        #检查md5值是否已经被处理
        if check_md5(md5_hex):
            return "文件已存在"
        if len(data) > config.max_split_char_number:#如果字符串的长度大于最大分割字符数,则进行分割
            knowledge_chunks:list[str] = self.spliter.split_text(data)#将字符串分割为多个文本块
        else:
            knowledge_chunks:list[str] = [data]#如果字符串的长度小于等于最大分割字符数,则直接将字符串作为文本块
#设置元数据,相当于每段数据的身份证
        metadata = {
        "source":filename,
        "create_time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),#格式化时间
        "md5":md5_hex,
                    }
        #内容存储到向量数据库
        self.chroma.add_texts(
            knowledge_chunks,#文本块列表
            metadatas = [metadata for _ in knowledge_chunks],#每个文本块的元数据列表
        )
        save_md5(md5_hex)
        return "文本上传成功"

    def upload_by_file(self, file_bytes: bytes, filename: str) -> str:
        """
        【便捷方法】从文件二进制内容直接上传到知识库，不用自己手动调 extract_text_from_file
        内部流程：① 调 extract_text_from_file 解析文件 → ② 调 upload_by_str 向量化入库
        :param file_bytes: 文件二进制内容（Streamlit 上传文件的 .getvalue()）
        :param filename:   文件名（带后缀，比如 "员工手册.docx"）
        :return:           上传结果字符串；如果解析后是空内容，会返回提示信息，不会入库
        """
        # 第一步：根据文件后缀自动选解析器，把二进制内容解析成纯文本
        file_text = extract_text_from_file(file_bytes, filename)

        if not file_text or not file_text.strip():
            # 解析后没内容（常见原因：扫描版PDF纯图片、加密PDF、文件损坏）
            return f"文件解析失败：未提取到可读文本内容，请检查文件是否是扫描版/加密/损坏(支持的格式：.txt/.csv/.pdf/.doc/.docx/.xls/.xlsx)"

        # 第二步：把解析好的纯文本交给 upload_by_str 入库（MD5去重 + 分块 + 向量化 + 存Chroma）
        return self.upload_by_str(file_text, filename)


if __name__ == "__main__":
    kb = KnowledgeBaseService()
    print(kb.upload_by_str("你好","test.txt"))
