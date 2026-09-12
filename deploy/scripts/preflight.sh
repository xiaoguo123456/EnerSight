#!/usr/bin/env bash
# 检查配置和资源边界，不打印环境变量或应用日志。
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null
docker compose version >/dev/null
command -v curl >/dev/null
[[ -f .env && "$(stat -c '%a' .env)" == 600 ]] || { echo '.env 不存在或权限不是 600'; exit 1; }
[[ "${ENERSIGHT_IMAGE:-}" == *@sha256:* ]] || { echo '缺少固定镜像摘要'; exit 1; }
python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import urlsplit
values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
environment = os.environ.get('ENERSIGHT_DEPLOY_ENV', 'prod')
assert environment in ('prod', 'test'), '未知部署环境'
assert values.get('ENERSIGHT_DEBUG') == 'false', '公网环境禁止开发模式'
url = urlsplit(values.get('ENERSIGHT_DATABASE_URL', ''))
assert url.scheme == 'postgresql+asyncpg' and url.path == f'/enersight_{environment}', '必须使用对应环境的独立数据库'
assert url.username == f'enersight_{environment}_app', '必须使用专属应用账号'
assert len(values.get('ENERSIGHT_JWT_SECRET', '')) >= 32 and not values['ENERSIGHT_JWT_SECRET'].startswith('dev-'), 'JWT 密钥不合格'
assert values.get('ENERSIGHT_ROOT_PATH') == '/enersight', '公网路径前缀不匹配'
assert int(values.get('ENERSIGHT_DB_POOL_SIZE', '2')) + int(values.get('ENERSIGHT_DB_MAX_OVERFLOW', '1')) <= 3, '共享 RDS 最多占用 3 个业务连接'
PY
PORT=${ENERSIGHT_DEPLOY_PORT:-8001}
if ss -lntH | awk '{print $4}' | grep -q ":${PORT}$"; then
  [[ -n "$(docker compose ps -q api)" ]] || { echo "$PORT 已被其他服务占用"; exit 1; }
fi
install -d -o 10001 -g 10001 -m 750 data
docker compose config --quiet
echo '部署预检通过。'
