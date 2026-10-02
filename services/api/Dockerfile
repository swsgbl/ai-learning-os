FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# M8-00: 版本真相源随镜像打包 —— GET /api/v1/version 在容器内可读
COPY VERSION ./VERSION
COPY services/api/app ./app
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/alembic ./alembic
# M14-210: 冒烟脚本随镜像打包 —— provider-smoke 证据编排在容器 /app 布局
# 下按 VERSION + infra 标记定位仓库根并编排 infra/smoke_*.sh
COPY infra/smoke_*.sh ./infra/
# M14-211: 本地语音冒烟探针随镜像打包 —— infra/smoke_voice_local.sh 以
# cwd=/app + 相对路径 exec tools/voice/smoke_local_voice.py，容器内必须可见
# 该探针文件（仅此一个轻量 .py；voice 模型/checkpoint/log/artifact/temp/
# .verify/.claude 一律不打包——见 .dockerignore）。
COPY tools/voice/smoke_local_voice.py ./tools/voice/smoke_local_voice.py

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
