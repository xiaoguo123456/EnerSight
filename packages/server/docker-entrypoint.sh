#!/bin/sh
# 启动前把数据库迁到最新；迁移失败就不要起服务，让健康检查暴露问题。
set -eu
cd /app
if [ "${ENERSIGHT_SKIP_MIGRATE:-false}" != "true" ]; then
  alembic upgrade head
fi
exec "$@"
