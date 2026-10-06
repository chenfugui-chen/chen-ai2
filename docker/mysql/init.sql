-- MySQL 容器首次启动时自动执行（挂载到 /docker-entrypoint-initdb.d）
-- 这里只负责建表；数据库本身由官方镜像根据 MYSQL_DATABASE 环境变量自动创建

CREATE DATABASE IF NOT EXISTS ai_rag DEFAULT CHARSET utf8mb4;
USE ai_rag;

CREATE TABLE IF NOT EXISTS conversation (
    id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(255),
    created_at DATETIME,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS message (
    id INT PRIMARY KEY AUTO_INCREMENT,
    conversation_id INT,
    role VARCHAR(20),
    content TEXT,
    created_at DATETIME,
    FOREIGN KEY (conversation_id) REFERENCES conversation(id) ON DELETE CASCADE
);

-- 长期记忆表：供 Agent 的 remember / recall 工具读写（mem_key 唯一，同 key 覆盖）
CREATE TABLE IF NOT EXISTS memory (
    id INT PRIMARY KEY AUTO_INCREMENT,
    mem_key VARCHAR(255) NOT NULL UNIQUE,
    mem_value TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
