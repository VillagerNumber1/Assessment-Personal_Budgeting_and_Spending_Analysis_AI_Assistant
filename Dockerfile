# 记账小助手 · 网页版镜像
#
# 构建：docker compose up --build
# 或者：docker build -t bill-assistant .
#
# 只装网页版需要的依赖（requirements-web.txt），
# 桌面版的 whisper / sounddevice 那套不进镜像。

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 换源用参数，国内默认清华，境外构建时覆盖成官方源
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements-web.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements-web.txt

# 只复制运行需要的文件（.dockerignore 已经把模型、录音、虚拟环境挡掉了）
COPY app.py parser.py storage.py asr.py config.py audio_effectsRecovery.py ./
COPY templates ./templates
COPY audio_effects ./audio_effects

# data/ 用挂载卷，别打进镜像：数据库和录音要活在容器外面
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
