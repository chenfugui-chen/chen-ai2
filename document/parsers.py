"""
文档解析层（Agent 的底层文件读取能力）

职责：把任意格式的文件二进制内容解析成纯文本。
Agent / Tool 层只调用本模块，不关心 PDF / DOCX 的底层解析细节。

支持的格式：.txt / .md / .csv / .pdf / .doc / .docx / .xls / .xlsx
（从 knowledge_base.py 抽取而来，原 RAG 模块继续复用同一份实现，保证解析行为一致）
"""

import os
import io
import csv
import tempfile
import pdfplumber                          # PDF 解析：逐页抽取文字，保留排版
from docx import Document                  # Word(.docx) 解析：读段落和表格
from openpyxl import load_workbook         # Excel(.xlsx) 解析：逐行逐单元格读取
import xlrd                                # Excel(.xls) 解析：旧版二进制格式


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
    用 pdfplumber 逐页抽取文本，每页之间用 "\\n\\n--- 分页符 ---\\n\\n" 分隔
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
    每个 Sheet 单独处理，Sheet 之间用 "\\n\\n=== Sheet: xxx ===\\n" 分隔
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
    【对外统一入口】根据文件后缀自动选择解析器，把文件的二进制内容提取为纯文本字符串
    支持的后缀：.txt / .md / .csv / .pdf / .doc / .docx / .xls / .xlsx（大小写不敏感）
    :param file_bytes:  文件的二进制内容
    :param file_name:   文件名（带后缀，比如 "产品手册.pdf"）
    :return:            解析后的纯文本字符串；如果格式不支持或解析失败，返回空字符串 ""
    """
    # 统一把后缀转成小写，避免 .PDF / .Pdf / .pdf 大小写不匹配
    suffix = os.path.splitext(file_name)[1].lower()

    try:
        if suffix in (".txt", ".md"):
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
            raise ValueError(f"暂不支持的文件格式：{suffix}，目前支持 .txt / .md / .csv / .pdf / .doc / .docx / .xls / .xlsx")
    except Exception as e:
        # 解析出错别抛异常崩掉，打印日志后返回空字符串，让上层好处理
        print(f"[文件解析失败] 文件名={file_name}，错误信息={e}")
        return ""


def read_document(file_path: str) -> str:
    """
    【对外统一入口·按路径读取】读取磁盘上的一个文件并解析成纯文本
    :param file_path: 文件的绝对路径或相对路径
    :return:          解析后的纯文本；文件不存在 / 内容为空时抛异常，由上层工具决定如何提示
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在：{file_path}")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    if not file_bytes:
        raise ValueError(f"文件为空（0 字节）：{file_path}")

    text = extract_text_from_file(file_bytes, os.path.basename(file_path))
    if not text or not text.strip():
        raise ValueError(f"未能从文件中提取到可读文本（可能是扫描件/加密/损坏）：{file_path}")
    return text
