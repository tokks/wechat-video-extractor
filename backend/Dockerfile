FROM python:3.11-slim

# 安装 ffmpeg（音频提取必需）
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app.py .

# 创建运行时目录
RUN mkdir -p uploads tasks

# 暴露端口
EXPOSE 8000

# Render 会设置 PORT 环境变量
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
