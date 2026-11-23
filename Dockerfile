# 记账小助手 · 网页版镜像
#
# 构建：docker compose up --build
# 或者：docker build -t bill-assistant .
#
# 只装网页版需要的依赖（requirements-web.txt），
# 桌面版的 whisper / sounddevice 那套不进镜像。

# 基础镜像地址可替换：国内直连 docker.io 经常超时，
# 可以用 --build-arg BASE_IMAGE=镜像加速地址 覆盖。
ARG BASE_IMAGE=python:3.14-slim

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 不要继承宿主机的本地代理：127.0.0.1 在容器里指向容器自己，会让 pip 一个包都拉不到
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY=*

WORKDIR /app

# 换源用参数，国内默认清华，境外构建时覆盖成官方源
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements-web.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements-web.txt

# 复制运行需要的文件（.dockerignore 已经把模型、录音、虚拟环境挡掉了）
# 用通配符而不是手写清单：以前手写时漏了 query.py，容器起来就 ModuleNotFoundError
COPY *.py ./
COPY templates ./templates
COPY audio_effects ./audio_effects

# data/ 用挂载卷，别打进镜像：数据库和录音要活在容器外面
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
