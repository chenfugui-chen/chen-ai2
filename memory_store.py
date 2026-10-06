"""
长期记忆存储（MySQL）

两张表分工：
- memory 表：键值对形式的长期记忆，供 Agent 的 remember / recall 工具读写
  （mem_key 建唯一索引，写入用 INSERT ... ON DUPLICATE KEY UPDATE 实现「记住/更新」）
- message 表：历史对话，供 search_history 工具做关键词检索（复用 HistoryStore 写入的数据）

表在首次使用时自动创建（CREATE TABLE IF NOT EXISTS），无需手动迁移。
"""

from contextlib import contextmanager
from datetime import datetime

import pymysql

import config_data as config


class MemoryStore:
    """长期记忆存储服务：remember / recall / search_history"""

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
        self._ensure_table()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接，退出 with 时自动关闭"""
        conn = pymysql.connect(**self.db_config)
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        """确保 memory 表存在（幂等，已有则跳过）"""
        sql = (
            "CREATE TABLE IF NOT EXISTS memory ("
            "  id INT PRIMARY KEY AUTO_INCREMENT,"
            "  mem_key VARCHAR(255) NOT NULL UNIQUE,"
            "  mem_value TEXT,"
            "  created_at DATETIME,"
            "  updated_at DATETIME"
            ") DEFAULT CHARSET=utf8mb4"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    # ========== 记忆（memory）相关 ==========

    def remember(self, key: str, value: str) -> str:
        """写入或更新一条记忆（同 key 覆盖）"""
        key = (key or "").strip()
        value = (value or "").strip()
        if not key:
            return "记忆写入失败：key 不能为空。"
        if not value:
            return "记忆写入失败：value 不能为空。"

        now = datetime.now()
        sql = (
            "INSERT INTO memory (mem_key, mem_value, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE mem_value = VALUES(mem_value), updated_at = VALUES(updated_at)"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (key, value, now, now))
            conn.commit()
        return f"已记住：{key} = {value}"

    def recall(self, key: str = "") -> list[dict]:
        """
        回忆记忆：
        - 传 key：先按 key 精确匹配，匹配不到再按 key 模糊匹配
        - 不传 key：返回最近更新的若干条记忆
        """
        key = (key or "").strip()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if key:
                    cur.execute(
                        "SELECT mem_key, mem_value, updated_at FROM memory WHERE mem_key = %s",
                        (key,),
                    )
                    rows = cur.fetchall()
                    if not rows:
                        cur.execute(
                            "SELECT mem_key, mem_value, updated_at FROM memory "
                            "WHERE mem_key LIKE %s OR mem_value LIKE %s "
                            "ORDER BY updated_at DESC LIMIT %s",
                            (f"%{key}%", f"%{key}%", config.memory_recall_limit),
                        )
                        rows = cur.fetchall()
                else:
                    cur.execute(
                        "SELECT mem_key, mem_value, updated_at FROM memory "
                        "ORDER BY updated_at DESC LIMIT %s",
                        (config.memory_recall_limit,),
                    )
                    rows = cur.fetchall()
        for row in rows:
            if isinstance(row.get("updated_at"), datetime):
                row["updated_at"] = row["updated_at"].strftime("%Y-%m-%d %H:%M")
        return rows

    # ========== 历史检索（search_history）相关 ==========

    def search_history(self, query: str, limit: int = 5) -> list[dict]:
        """在历史对话中按关键词检索（跨所有会话），按时间倒序返回"""
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 20))
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, content, created_at FROM message "
                    "WHERE content LIKE %s ORDER BY id DESC LIMIT %s",
                    (f"%{query}%", limit),
                )
                rows = cur.fetchall()
        for row in rows:
            if isinstance(row.get("created_at"), datetime):
                row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M")
        return rows


if __name__ == "__main__":
    store = MemoryStore()
    print(store.remember("用户姓名", "陈欣凯"))
    print(store.recall())
    print(store.recall("姓名"))
    print(store.search_history("项目"))