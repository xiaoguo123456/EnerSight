# EnerSight BFF 生产镜像。docs/10-deployment.md
# 多阶段：builder 装依赖（含编译工具），runtime 只带 .venv 与代码，非 root 运行。
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 依赖层只取清单，业务代码变化不重新生成虚拟环境
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime

# opencv-headless / scipy 的运行时共享库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 libexpat1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 enersight

WORKDIR /app
COPY --from=builder --chown=enersight:enersight /app/.venv /app/.venv
COPY --chown=enersight:enersight app /app/app
COPY --chown=enersight:enersight scripts/prepare_map.py scripts/check_map_runtime.py /app/scripts/
COPY --chown=enersight:enersight alembic /app/alembic
COPY --chown=enersight:enersight alembic.ini /app/alembic.ini
COPY --chown=enersight:enersight docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh && mkdir -p /app/data && chown enersight:enersight /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ENERSIGHT_DEBUG=false \
    ENERSIGHT_DATABASE_URL=sqlite+aiosqlite:////app/data/enersight.db \
    TZ=Asia/Shanghai
# 上面的 SQLite 只是让 debug 冒烟能起来（/app 只有 data/ 可写）；生产必须覆盖为 PostgreSQL，
# main._check_production_config 在 debug=false 时会拒绝 SQLite。

USER enersight
EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ready >/dev/null || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
