# 个人 AI 助手（RAG + Agent）生产镜像
FROM python:3.13-slim

# PYTHONUNBUFFERED：日志直接进容器 stdout，便于 docker logs 查看
# PYTHONDONTWRITEBYTECODE：不在容器里生成 .pyc
# PIP_NO_CACHE_DIR：不保留 pip 缓存，减小镜像体积
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# 先只拷贝依赖清单再安装，利用 Docker 层缓存：requirements.txt 不变就无需重装
# 用阿里云 PyPI 镜像：国内服务器直连 pypi.org 很慢、容易构建超时
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 再把项目代码打进镜像（运行时会被 compose 的项目目录挂载覆盖，见 docker-compose.yml）
COPY . .

EXPOSE 8501 8000

# 同一容器内启动两个进程：
#   1) FastAPI 后端（127.0.0.1:8000）—— 提供 SSE 事件流接口，仅容器内可见
#   2) Streamlit 前端（0.0.0.0:8501）—— 消费后端 SSE 渲染界面，对外暴露
# uvicorn 后台运行，streamlit 用 exec 作为 PID 1 接管容器
CMD ["sh", "-c", "uvicorn api_server:app --host 127.0.0.1 --port 8000 --log-level warning & exec streamlit run app_web.py"]
