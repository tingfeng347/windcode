# syntax=docker/dockerfile:1
# 默认通过 DaoCloud 的国内镜像站拉取 Docker Hub 官方 Python 镜像。
# 在海外构建环境可使用 --build-arg PYTHON_IMAGE=python:3.11-slim 覆盖。
ARG PYTHON_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv==0.7.13"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md LICENSE hatch_build.py ./
COPY src ./src
RUN uv sync --frozen --no-dev


FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.source="https://github.com/tingfeng347/windcode" \
    org.opencontainers.image.description="A safe, extensible terminal coding agent" \
    org.opencontainers.image.licenses="Apache-2.0"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/windcode

RUN groupadd --gid 10001 windcode \
    && useradd --uid 10001 --gid windcode --create-home --shell /usr/sbin/nologin windcode \
    && mkdir /workspace \
    && chown windcode:windcode /workspace

COPY --from=builder /app /app
COPY docker-entrypoint.sh /usr/local/bin/windcode-entrypoint
RUN chmod 755 /usr/local/bin/windcode-entrypoint

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/windcode-entrypoint"]
CMD ["windcode", "/workspace"]
