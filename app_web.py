import os
import streamlit as st
from knowledge_base import KnowledgeBaseService, extract_text_from_file  # extract_text_from_file 用来把上传的文件解析成纯文本给用户预览
from history_store import HistoryStore
from rag import RagService


# ===== 全局对象初始化（仅运行一次） =====
if "service" not in st.session_state:
    st.session_state["service"] = KnowledgeBaseService()
if "history_store" not in st.session_state:
    st.session_state["history_store"] = HistoryStore()
if "current_conv_id" not in st.session_state:
    st.session_state["current_conv_id"] = None


# ===== 侧边栏：历史会话管理 =====
with st.sidebar:
    st.title("💬 历史会话")

    if st.button("➕ 新建会话", use_container_width=True):
        st.session_state["current_conv_id"] = None
        st.rerun()

    st.divider()

    convs = st.session_state["history_store"].list_conversations()
    if not convs:
        st.caption("暂无历史会话")
    for conv in convs:
        col1, col2 = st.columns([5, 1])
        with col1:
            display_title = conv["title"] if len(conv["title"]) <= 16 else conv["title"][:16] + "..."
            is_active = st.session_state["current_conv_id"] == conv["id"]
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"💬 {display_title}", key=f"conv_{conv['id']}", use_container_width=True, type=btn_type):
                st.session_state["current_conv_id"] = conv["id"]
                st.rerun()
        with col2:
            if st.button("🗑️", key=f"del_{conv['id']}"):
                st.session_state["history_store"].delete_conversation(conv["id"])
                if st.session_state["current_conv_id"] == conv["id"]:
                    st.session_state["current_conv_id"] = None
                st.rerun()


# ===== 主界面 =====
st.title("📚 AI知识库问答系统")

# ===== 区域1：上传知识库 =====
with st.expander("📤 上传知识库文件", expanded=False):
    st.caption("💡 支持格式：.txt（记事本）/ .csv / .pdf / .doc / .docx（Word）/ .xls / .xlsx（Excel）")
    uploaded_file = st.file_uploader(
        "上传文件",
        type = ["txt", "csv", "pdf", "doc", "docx", "xls", "xlsx"],  # 支持 7 种常见文档格式（含旧版 doc/xls、CSV）
        accept_multiple_files = False,
    )

    if uploaded_file is not None:
        file_name = uploaded_file.name
        file_type = uploaded_file.type
        file_size = uploaded_file.size
        file_bytes = uploaded_file.getvalue()  # 拿二进制原始内容（pdf/docx/xlsx 不能直接 decode utf-8，会报错！）

        # 第一步：把二进制内容根据后缀解析成纯文本（内部自动选 txt/pdf/docx/xlsx 解析器）
        file_text = extract_text_from_file(file_bytes, file_name)

        st.subheader(f"📄 文件名：{file_name}")
        size_kb = file_size / 1024  # 字节转 KB，原来显示 B 太小看不清
        st.write(f"文件类型：{file_type}，大小：{size_kb:.1f} KB")

        # 第二步：显示解析后的文本预览（让用户确认解析是否正确，再决定要不要入库）
        if not file_text or not file_text.strip():
            # 解析后空内容：大概率是扫描版PDF（纯图片），或者加密/损坏文件
            st.error("❌ 文件解析失败：未提取到可读文本内容")
            st.caption("可能原因：① PDF 是扫描件/纯图片（需先做 OCR）；② 文件加密；③ 文件已损坏")
        else:
            # 解析成功：显示内容预览折叠面板
            st.success(f"✅ 文件解析成功，共提取到约 {len(file_text)} 个字符")
            with st.expander("👁️ 查看解析后的文件内容", expanded=False):
                st.code(file_text, language=None)  # 用 code 块显示长文本更整洁，不乱换行

            # 第三步：调用 KnowledgeBaseService 的 upload_by_str 入库
            # （MD5去重 + 分块 + 向量化 + 存Chroma，逻辑和之前完全一样）
            result = st.session_state["service"].upload_by_str(file_text, file_name)
            if "成功" in result:
                st.success(f"🎉 {result}")
            else:
                st.warning(f"⚠️ {result}")

    # ===== 文件夹批量导入 =====
    st.divider()
    st.caption("📁 批量导入：填写本机文件夹路径，自动递归扫描并导入该文件夹下所有支持格式的文件（.txt/.pdf/.doc/.docx/.xls/.xlsx）")

    folder_path = st.text_input(
        "文件夹路径",
        placeholder=r"例如：D:\资料\产品手册",
        key="folder_path_input",
    )

    if st.button("🚀 扫描并批量导入", use_container_width=True):
        if not folder_path or not os.path.isdir(folder_path):
            # 路径为空或不存在，直接提示，不往下走
            st.error(f"❌ 文件夹不存在或不是有效目录：{folder_path}")
        else:
            # 递归扫描该文件夹（含子文件夹）下所有支持格式的文件
            supported_suffix = {".txt", ".csv", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}
            file_list = []
            for root, _, files in os.walk(folder_path):
                for name in files:
                    # 跳过 Word/Excel 生成的临时锁文件（~$ 开头），它们不是真正的文档
                    if name.startswith("~$"):
                        continue
                    if os.path.splitext(name)[1].lower() in supported_suffix:
                        file_list.append(os.path.join(root, name))

            if not file_list:
                st.warning("⚠️ 该文件夹下没有找到支持格式的文件")
            else:
                st.info(f"共找到 {len(file_list)} 个文件，开始导入（大文件夹请耐心等待）...")
                progress = st.progress(0.0)
                ok, skip, fail = 0, 0, 0
                fail_files = []
                # 逐个文件处理：每次只读一个文件进内存，200MB 文件夹也不会爆内存
                for idx, path in enumerate(file_list):
                    name = os.path.basename(path)
                    try:
                        with open(path, "rb") as f:
                            file_bytes = f.read()
                        file_text = extract_text_from_file(file_bytes, name)
                        if not file_text or not file_text.strip():
                            fail += 1
                            fail_files.append(f"{name}（未提取到文本，可能是扫描件/加密/损坏）")
                        else:
                            result = st.session_state["service"].upload_by_str(file_text, name)
                            if "成功" in result:
                                ok += 1
                            elif "已存在" in result:
                                skip += 1  # MD5 去重命中，说明之前已导入过
                            else:
                                fail += 1
                                fail_files.append(f"{name}（{result}）")
                    except Exception as e:
                        fail += 1
                        fail_files.append(f"{name}（读取失败：{e}）")
                    progress.progress((idx + 1) / len(file_list), text=f"已处理 {idx + 1}/{len(file_list)}：{name}")

                st.success(f"🎉 导入完成：成功 {ok} 个，跳过（已存在）{skip} 个，失败 {fail} 个")
                if fail_files:
                    with st.expander(f"⚠️ 查看失败的 {len(fail_files)} 个文件", expanded=False):
                        for item in fail_files:
                            st.write(f"· {item}")


# ===== 区域2：聊天问答 =====
st.divider()
st.subheader("💡 智能问答")

if st.session_state["current_conv_id"] is not None:
    history = st.session_state["history_store"].get_history_messages(st.session_state["current_conv_id"])
    for msg in history:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.write(msg["content"])
else:
    st.caption("💡 提示：在下方输入问题即可开启新会话，或从左侧选择历史会话")

user_input = st.chat_input("请输入你的问题")
if user_input:
    rag = RagService(conversation_id=st.session_state["current_conv_id"])

    with st.chat_message("user", avatar="🧑"):
        st.write(user_input)

    with st.spinner("🤖别急 我正在思考中..."):
        answer = rag.ask(user_input)

    with st.chat_message("assistant", avatar="🤖"):
        st.write(answer)

    st.session_state["current_conv_id"] = rag.conversation_id
    st.rerun()
