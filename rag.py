from vector_stores import VectorStoreService
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI
from history_store import HistoryStore
import config_data as config

class RagService(object):

    def __init__(self, conversation_id: int | None = None):
        self.history_store = HistoryStore()
        self.conversation_id = conversation_id

        self.vector_service = VectorStoreService()

        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", "根据我提供的已知资料为主，结合历史对话记录，简洁和专业地回答用户的提问。\n\n已知资料：{context}\n\n历史对话记录：\n{history}"),
            ("human", "用户当前问题：{input}"),
        ])

        self.chat_model = ChatOpenAI(
            model=config.deepseek_model,
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
        )

        self.chain  = self.__get_chain()

    def __get_chain(self):
        retriever = self.vector_service.get_retriever()

        def format_document(docs: list[Document]):
            if not docs:
                return "没有相关资料"
            formatted_str = ""
            for doc in docs:
                formatted_str += f"文档片段:{doc.page_content}\n文档元数据:{doc.metadata}\n\n"
            return formatted_str

        chain = (
            RunnablePassthrough()
            | {
                "input": lambda x: x["input"],
                "history": lambda x: x["history"],
                "context": lambda x: format_document(retriever.invoke(x["input"])),
            }
            | self.prompt_template | self.chat_model
        )
        return chain

    def ask(self, user_input: str) -> str:
        if self.conversation_id is None:
            self.conversation_id = self.history_store.create_conversation(user_input)

        history_msgs = self.history_store.get_history_messages(self.conversation_id)
        history_str = HistoryStore.format_history_for_prompt(history_msgs)

        result = self.chain.invoke({
            "input": user_input,
            "history": history_str,
        })
        answer = result.content if hasattr(result, "content") else str(result)

        self.history_store.add_message(self.conversation_id, "user", user_input)
        self.history_store.add_message(self.conversation_id, "assistant", answer)

        return answer

if __name__ == "__main__":
    rag = RagService()
    print("=== 第1轮 ===")
    print(rag.ask("我身高175,体重180斤,穿什么尺码？"))
    print("\n=== 第2轮（测试历史对话，AI应该知道'它'指代上一轮的衣服） ===")
    print(rag.ask("那它怎么洗不会变形？"))
    print("\n当前会话ID：", rag.conversation_id)
