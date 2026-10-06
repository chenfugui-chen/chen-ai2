"""
FastAPI 后端服务（前后端分离里的「后端」）

职责：
1. 对话：POST /chat/stream —— 以 SSE 推送 Agent 的结构化事件流
   （meta / tool_call / tool_result / answer / usage / done / error）
2. 会话：会话列表、历史消息、删除（前端不再直连数据库）

启动：uvicorn api_server:app --host 127.0.0.1 --port 8000
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.personal_agent import PersonalAssistantAgent
from history_store import HistoryStore

app = FastAPI(title="aiRAG API")

# 允许前端（Streamlit/浏览器）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 重对象（含大模型客户端 + 资料索引）全局单例，避免每次请求都重建
_agent: PersonalAssistantAgent | None = None
_history: HistoryStore | None = None


def get_agent() -> PersonalAssistantAgent:
    global _agent
    if _agent is None:
        _agent = PersonalAssistantAgent()
    return _agent


def get_history() -> HistoryStore:
    global _history
    if _history is None:
        _history = HistoryStore()
    return _history


def _sse(event: str, data: dict) -> str:
    """把一条事件编码成 SSE 帧（event + data + 空行）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """对话主入口：以 SSE 流式返回 Agent 的事件流"""

    def event_generator():
        try:
            for ev in get_agent().stream(req.message, req.conversation_id):
                yield _sse(ev["event"], ev["data"])
        except Exception as e:
            # 任何异常都以 error 事件返回，不中断连接、不 500
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证逐帧下发
        },
    )


@app.get("/conversations")
def list_conversations() -> list[dict]:
    return get_history().list_conversations()


class MatchRequest(BaseModel):
    job_description: str


@app.post("/match")
def match(req: MatchRequest) -> dict:
    """岗位匹配分析：HR 粘贴岗位职责，返回匹配度分析文本"""
    try:
        return {"analysis": get_agent().match_job(req.job_description)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: int) -> list[dict]:
    return get_history().get_history_messages(conv_id)


@app.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int) -> dict:
    get_history().delete_conversation(conv_id)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)