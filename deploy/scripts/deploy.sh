#!/usr/bin/env sh
# 预检 → 拉固定镜像 → 原地更新容器 → 等健康检查。只允许在 deploy 目录内执行。
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

"$SCRIPT_DIR/preflight.sh"

docker compose pull api
docker compose up -d api

ATTEMPT=1
MAX_ATTEMPTS=36
while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then
    docker compose ps
    echo "部署完成：EnerSight 健康检查已通过。"
    exit 0
  fi
  sleep 5
  ATTEMPT=$((ATTEMPT + 1))
done

echo "部署失败：等待 180 秒后健康检查仍未通过。" >&2
docker compose ps >&2
docker compose logs --tail=200 api >&2
exit 1
