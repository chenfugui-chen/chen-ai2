import json

import requests
import streamlit as st

import config_data as config

# 后端 API（FastAPI）地址：对话走 SSE、会话管理走 REST，前端不直连数据库、不持有大模型密钥
API_BASE = config.api_base_url


# ===== 后端 API 调用 =====

def api_list_conversations() -> list[dict]:
    """会话列表"""
    try:
        resp = requests.get(f"{API_BASE}/conversations", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.sidebar.error(f"获取会话列表失败：{e}")
        return []


def api_get_messages(conv_id: int) -> list[dict]:
    """某个会话的历史消息"""
    try:
        resp = requests.get(f"{API_BASE}/conversations/{conv_id}/messages", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"获取历史消息失败：{e}")
        return []


def api_delete_conversation(conv_id: int) -> None:
    """删除会话"""
    try:
        requests.delete(f"{API_BASE}/conversations/{conv_id}", timeout=15)
    except Exception as e:
        st.sidebar.error(f"删除会话失败：{e}")


def stream_chat(message: str, conversation_id: int | None):
    """
    调用后端 SSE 接口，逐条产出事件 dict：{"event": 事件名, "data": 数据}。
    事件：meta / tool_call / tool_result / answer / usage / done / error
    """
    with requests.post(
        f"{API_BASE}/chat/stream",
        json={"message": message, "conversation_id": conversation_id},
        stream=True,
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        event_name = "message"
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("event:"):
                event_name = raw[len("event:"):].strip()
            elif raw.startswith("data:"):
                payload = raw[len("data:"):].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                yield {"event": event_name, "data": data}


# ===== 全局状态 =====
if "current_conv_id" not in st.session_state:
    st.session_state["current_conv_id"] = None


# ===== 侧边栏：历史会话管理 =====
with st.sidebar:
    st.title("💬 历史会话")

    if st.button("➕ 新建会话", use_container_width=True):
        st.session_state["current_conv_id"] = None
        st.rerun()

    st.divider()

    convs = api_list_conversations()
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
                api_delete_conversation(conv["id"])
                if st.session_state["current_conv_id"] == conv["id"]:
                    st.session_state["current_conv_id"] = None
                st.rerun()


# ===== 主界面：与个人 AI 助手对话 =====
st.title("🤖 陈欣凯的个人 AI 助手")
st.caption("可以问我：你是谁 / 你的专业是什么 / 你做过哪些项目 / 你的实习经历 / 你掌握哪些技术")

st.info(
    "👋 您好！我是陈欣凯的 AI 数字分身，只回答与「他的经历」和「已导入知识库」相关的问题。\n\n"
    "**给 HR：** 想快速判断他是否适合某个岗位？把**岗位职责 / 任职要求**粘贴到下方"
    "「🎯 岗位匹配分析」中，我会结合他的真实资料给出匹配度分析。"
)

# ===== 岗位匹配分析（HR 粘贴岗位职责） =====
with st.expander("🎯 岗位匹配分析（HR 专用）", expanded=False):
    st.caption("把岗位职责 / 任职要求粘贴到下面，我会结合陈欣凯的个人资料分析匹配度，重点突出相符的经历与技术点")
    jd_text = st.text_area(
        "岗位职责 / 任职要求",
        height=200,
        key="jd_text",
        placeholder="粘贴招聘 JD 中的岗位职责与任职要求……",
    )
    if st.button("🔍 分析匹配度", key="jd_analyze"):
        if not jd_text.strip():
            st.warning("请先粘贴岗位职责 / 任职要求")
        else:
            with st.spinner("正在结合个人资料分析匹配度（约十几秒）……"):
                try:
                    resp = requests.post(f"{API_BASE}/match", json={"job_description": jd_text}, timeout=300)
                    resp.raise_for_status()
                    result = resp.json()
                except Exception as e:
                    result = {"error": str(e)}
            if result.get("error"):
                st.error(f"❌ 分析失败：{result['error']}")
            else:
                st.success("✅ 分析完成")
                st.markdown(result.get("analysis", ""))

if st.session_state["current_conv_id"] is not None:
    history = api_get_messages(st.session_state["current_conv_id"])
    for msg in history:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.write(msg["content"])

    # 最近一次回答的「执行链路」：默认折叠，点开可看每步（工具调用 / 结果 / token 用量）
    last_chain = st.session_state.get("last_chain")
    if last_chain and last_chain.get("conv_id") == st.session_state["current_conv_id"] and last_chain.get("steps"):
        with st.expander("🔗 执行链路（本次回答是如何得到的）", expanded=False):
            for step in last_chain["steps"]:
                st.markdown(step)
else:
    st.caption("💡 提示：在下方输入问题即可开启新会话，或从左侧选择历史会话")

user_input = st.chat_input("请输入你的问题")
if user_input:
    with st.chat_message("user", avatar="🧑"):
        st.write(user_input)

    with st.chat_message("assistant", avatar="🤖"):
        # 思考过程中实时展示工具调用；完成后收起，改由下方「执行链路」面板保留
        status = st.status("🤖 正在查阅资料并思考…", expanded=True)
        answer_box = st.empty()
        answer_text = ""
        chain = []  # 记录本次回答的执行链路，供回答完成后折叠展示
        conv_id = st.session_state["current_conv_id"]
        errored = None

        try:
            for ev in stream_chat(user_input, conv_id):
                name, data = ev["event"], ev["data"]
                if name == "meta":
                    conv_id = data.get("conversation_id", conv_id)
                elif name == "tool_call":
                    step = f"🔧 调用工具 `{data.get('name')}`：`{data.get('args')}`"
                    status.write(step)
                    chain.append(step)
                elif name == "tool_result":
                    step = f"📄 `{data.get('name')}` 返回 {data.get('chars')} 字"
                    status.write(step)
                    chain.append(step)
                elif name == "answer":
                    # 打字机效果：每来一段增量就重渲染
                    answer_text += data.get("delta", "")
                    answer_box.markdown(answer_text + "▌")
                elif name == "usage":
                    step = f"🧮 token 用量：{data}"
                    status.write(step)
                    chain.append(step)
                elif name == "error":
                    errored = data.get("message", "未知错误")
        except Exception as e:
            errored = f"连接后端失败：{e}"

        if errored:
            answer_box.error(f"❌ {errored}")
            status.update(label="❌ 出错了", state="error", expanded=True)
        else:
            answer_box.markdown(answer_text)
            status.update(label="✅ 回答完成", state="complete", expanded=False)

    st.session_state["current_conv_id"] = conv_id
    st.session_state["last_chain"] = {"conv_id": conv_id, "steps": chain}
    st.rerun()