import pymysql
import config_data as config
from datetime import datetime


class HistoryStore:
    """MySQL 历史会话存储服务：负责会话和消息的增删改查"""

    def __init__(self):
        self.db_config = {
            "host": config.mysql_host,
            "port": config.mysql_port,
            "user": config.mysql_user,
            "password": config.mysql_password,
            "database": config.mysql_db,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
        }

    def _get_conn(self):
        """内部方法：获取数据库连接，用完自动关闭"""
        return pymysql.connect(**self.db_config)

    # ========== 会话（conversation）相关 ==========

    def create_conversation(self, first_user_msg: str) -> int:
        title = first_user_msg[:20].strip() if first_user_msg else "新会话"
        now = datetime.now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversation (title, created_at, updated_at) VALUES (%s, %s, %s)",
                    (title, now, now),
                )
                conn.commit()
                return cur.lastrowid

    def delete_conversation(self, conv_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM conversation WHERE id = %s", (conv_id,))
                conn.commit()

    def list_conversations(self) -> list[dict]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, updated_at FROM conversation ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
                for row in rows:
                    if isinstance(row.get("updated_at"), datetime):
                        row["updated_at"] = row["updated_at"].strftime("%Y-%m-%d %H:%M")
                return rows

    # ========== 消息（message）相关 ==========

    def add_message(self, conv_id: int, role: str, content: str) -> None:
        now = datetime.now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO message (conversation_id, role, content, created_at) VALUES (%s, %s, %s, %s)",
                    (conv_id, role, content, now),
                )
                cur.execute(
                    "UPDATE conversation SET updated_at = %s WHERE id = %s",
                    (now, conv_id),
                )
                conn.commit()

    def get_history_messages(self, conv_id: int) -> list[dict]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, content FROM message WHERE conversation_id = %s ORDER BY created_at ASC",
                    (conv_id,),
                )
                return cur.fetchall()

    @staticmethod
    def format_history_for_prompt(messages: list[dict]) -> str:
        if not messages:
            return "（暂无历史对话）"
        lines = []
        for msg in messages:
            role_cn = "用户" if msg["role"] == "user" else "AI" if msg["role"] == "assistant" else "系统"
            lines.append(f"{role_cn}：{msg['content']}")
        return "\n".join(lines)


if __name__ == "__main__":
    print("开始测试 HistoryStore ...")
    store = HistoryStore()

    cid = store.create_conversation("测试一下历史功能好使不")
    print(f"✅ 新建会话成功，ID={cid}")

    store.add_message(cid, "user", "我身高175穿什么码？")
    store.add_message(cid, "assistant", "建议穿L码或者XL码")
    print("✅ 消息写入成功")

    msgs = store.get_history_messages(cid)
    print("✅ 历史消息：", msgs)
    print("   格式化后：")
    print(HistoryStore.format_history_for_prompt(msgs))

    convs = store.list_conversations()
    print(f"✅ 当前会话总数：{len(convs)}，最新一条标题：{convs[0]['title'] if convs else '无'}")

    print(f"\n⚠️  测试结束，如果要清理测试数据，请手动执行：DELETE FROM conversation WHERE id = {cid}")
