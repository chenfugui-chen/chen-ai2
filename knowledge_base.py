import os
import io
import csv
import config_data as config
import hashlib
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from datetime import datetime
import pdfplumber                          # PDF 解析：逐页抽取文字，保留排版
from docx import Document                  # Word(.docx) 解析：读段落和表格
from openpyxl import load_workbook         # Excel(.xlsx) 解析：逐行逐单元格读取
import xlrd                                # Excel(.xls) 解析：旧版二进制格式
import tempfile                            # 临时文件：.doc 需先落盘再交给 Word 处理



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


# ===================== 以下是文件解析工具函数（7种格式 + 统一入口） =====================

def _extract_txt_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析 txt 格式的二进制内容
    兼容 UTF-8 / GBK 两种常见编码，避免中文 txt 乱码
    """
    # 优先尝试 UTF-8（最常用）
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # UTF-8 失败则尝试 GBK（Windows 下中文 txt 常见编码）
    try:
        return file_bytes.decode("gbk")
    except UnicodeDecodeError:
        # 都失败则用 ignore 模式强行解码，保留大部分可读内容
        return file_bytes.decode("utf-8", errors="ignore")


def _extract_pdf_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析 PDF 格式的二进制内容
    用 pdfplumber 逐页抽取文本，每页之间用 "\n\n--- 分页符 ---\n\n" 分隔
    对于扫描版 PDF（纯图片无文字）会抽取到空字符串，这是正常的
    """
    all_text_parts = []
    # pdfplumber.open 支持传入二进制流（io.BytesIO），不用先落盘
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                all_text_parts.append(page_text)
    # 多页内容用分页标记连起来，保留文档的分页结构信息
    return "\n\n--- 分页符 ---\n\n".join(all_text_parts)


def _extract_docx_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析 Word(.docx) 格式的二进制内容
    依次读取：所有段落文本 → 所有表格的单元格内容
    段落之间用换行、表格的行之间用换行、单元格之间用" | "分隔
    注意：仅支持 .docx；旧版 .doc 请走 _extract_doc_bytes（先用 Word 转成 docx 再复用本方法）
    """
    all_text_parts = []
    doc = Document(io.BytesIO(file_bytes))

    # 第一步：读所有正文段落
    for para in doc.paragraphs:
        if para.text.strip():
            all_text_parts.append(para.text)

    # 第二步：读所有表格（很多 Word 的关键数据存在表格里，别漏掉）
    for table in doc.tables:
        for row in table.rows:
            row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_cells:
                all_text_parts.append(" | ".join(row_cells))

    return "\n".join(all_text_parts)


def _extract_doc_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析旧版 Word(.doc) 格式的二进制内容
    .doc 是二进制格式，python-docx 读不了，这里借助本机安装的 Microsoft Word（win32com 自动化）：
    ① 把二进制写成临时 .doc 文件 → ② 用 Word 另存为临时 .docx → ③ 复用 _extract_docx_bytes 解析
    仅支持 Windows 且已安装 Word
    """
    tmp_dir = tempfile.mkdtemp()
    doc_path = os.path.join(tmp_dir, "temp.doc")
    docx_path = os.path.join(tmp_dir, "temp.docx")
    import pythoncom
    import win32com.client
    word = None
    # COM 必须在当前线程初始化，否则在 Streamlit 子线程里调用 Word 会失败
    pythoncom.CoInitialize()
    try:
        # 第一步：把二进制内容落盘成临时 .doc
        with open(doc_path, "wb") as f:
            f.write(file_bytes)

        # 第二步：调用本机 Word 把 .doc 另存为 .docx（12 = wdFormatXMLDocument）
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False          # 后台运行，不弹窗
        word.DisplayAlerts = 0        # 关闭转换过程中的所有弹窗
        doc = word.Documents.Open(doc_path)
        doc.SaveAs(docx_path, FileFormat=12)
        doc.Close(False)

        # 第三步：读取转换后的 docx，复用已有的 docx 解析逻辑
        with open(docx_path, "rb") as f:
            docx_bytes = f.read()
        return _extract_docx_bytes(docx_bytes)
    finally:
        # 无论成功失败都要退出 Word 进程并释放 COM
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
        # 清理临时文件，避免堆积
        for path in (doc_path, docx_path):
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(tmp_dir)


def _extract_xlsx_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析 Excel(.xlsx) 格式的二进制内容
    每个 Sheet 单独处理，Sheet 之间用 "\n\n=== Sheet: xxx ===\n" 分隔
    每一行的单元格用" | "分隔，适合阅读和后续分块
    注意：仅支持 .xlsx；旧版 .xls 请走 _extract_xls_bytes
    """
    all_text_parts = []
    # data_only=True：如果单元格是公式，读公式计算后的结果值，不是公式本身
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        sheet_lines = []
        sheet_lines.append(f"=== Sheet: {sheet_name} ===")

        for row in sheet.iter_rows(values_only=True):
            # 过滤整行都是 None 的空行
            row_values = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
            if row_values:
                sheet_lines.append(" | ".join(row_values))

        # 只有 Sheet 里有内容才追加
        if len(sheet_lines) > 1:
            all_text_parts.append("\n".join(sheet_lines))

    return "\n\n".join(all_text_parts)


def _extract_xls_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析旧版 Excel(.xls) 格式的二进制内容
    用 xlrd 直接读二进制内容，输出结构与 _extract_xlsx_bytes 保持一致：
    每个 Sheet 单独处理，Sheet 之间用 "\\n\\n=== Sheet: xxx ===\\n" 分隔，每行单元格用 " | " 分隔
    """
    all_text_parts = []
    book = xlrd.open_workbook(file_contents=file_bytes)

    for sheet in book.sheets():
        sheet_lines = [f"=== Sheet: {sheet.name} ==="]

        for row_idx in range(sheet.nrows):
            row_values = []
            for cell in sheet.row(row_idx):
                value = cell.value
                # xlrd 把数字统一读成 float，整数要还原成 int，避免出现 "130.0" 这种尾巴
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                cell_str = str(value).strip()
                if cell_str:
                    row_values.append(cell_str)
            if row_values:
                sheet_lines.append(" | ".join(row_values))

        if len(sheet_lines) > 1:
            all_text_parts.append("\n".join(sheet_lines))

    return "\n\n".join(all_text_parts)


def _extract_csv_bytes(file_bytes: bytes) -> str:
    """
    【内部方法】解析 CSV(.csv) 格式的二进制内容
    先按编码解码成文本（兼容 UTF-8-BOM / UTF-8 / GBK），再用 csv 标准库按行解析
    每一行的单元格用 " | " 分隔，输出风格与 Excel 解析保持一致
    """
    # UTF-8-BOM 优先（Excel 导出的中文 CSV 常见），再退到普通 UTF-8、GBK
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("gbk")
        except UnicodeDecodeError:
            text = file_bytes.decode("utf-8", errors="ignore")

    all_text_parts = []
    # csv.reader 需要可迭代的行，用 splitlines() 拆行即可
    for row in csv.reader(text.splitlines()):
        row_values = [cell.strip() for cell in row if cell and cell.strip()]
        if row_values:
            all_text_parts.append(" | ".join(row_values))

    return "\n".join(all_text_parts)


def extract_text_from_file(file_bytes: bytes, file_name: str) -> str:
    """
    【对外统一入口】根据文件后缀自动选择解析器，把上传的文件二进制内容提取为纯文本字符串
    支持的后缀：.txt / .csv / .pdf / .doc / .docx / .xls / .xlsx（大小写不敏感）
    :param file_bytes:  文件的二进制内容（Streamlit 上传文件的 .getvalue()）
    :param file_name:   文件名（带后缀，比如 "产品手册.pdf"）
    :return:            解析后的纯文本字符串；如果格式不支持或解析失败，返回空字符串 ""
    """
    # 统一把后缀转成小写，避免 .PDF / .Pdf / .pdf 大小写不匹配
    suffix = os.path.splitext(file_name)[1].lower()

    try:
        if suffix == ".txt":
            return _extract_txt_bytes(file_bytes)
        elif suffix == ".csv":
            return _extract_csv_bytes(file_bytes)
        elif suffix == ".pdf":
            return _extract_pdf_bytes(file_bytes)
        elif suffix == ".docx":
            return _extract_docx_bytes(file_bytes)
        elif suffix == ".doc":
            return _extract_doc_bytes(file_bytes)
        elif suffix == ".xlsx":
            return _extract_xlsx_bytes(file_bytes)
        elif suffix == ".xls":
            return _extract_xls_bytes(file_bytes)
        else:
            # 不支持的文件格式（比如 .jpg 等非文档格式）
            raise ValueError(f"暂不支持的文件格式：{suffix}，目前支持 .txt / .csv / .pdf / .doc / .docx / .xls / .xlsx")
    except Exception as e:
        # 解析出错别抛异常崩掉，打印日志后返回空字符串，让上层好处理
        print(f"[文件解析失败] 文件名={file_name}，错误信息={e}")
        return ""


class KnowledgeBaseService(object):#知识库服务类:
    #负责知识库的上传,查询,删除等操作
    
    def __init__(self):#初始化知识库服务类
        os.makedirs(config.persist_directory,exist_ok=True)#传入向量数据库的存储路径
        self.chroma = Chroma(
            collection_name = config.collection_name,#数据库的表名
            embedding_function = config.get_embedding_function(),#本地向量嵌入函数
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