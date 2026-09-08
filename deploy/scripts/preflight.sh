#!/usr/bin/env sh
# 部署前检查。不输出任何敏感值。
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

fail() {
  echo "预检失败：$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未安装 Docker"
docker compose version >/dev/null 2>&1 || fail "Docker Compose 不可用"
command -v curl >/dev/null 2>&1 || fail "未安装 curl，无法执行健康检查"
[ -f .env ] || fail "缺少 .env，请从安全副本恢复"

ENV_MODE=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || true)
[ "$ENV_MODE" = "600" ] || fail ".env 权限应为 600，当前为 ${ENV_MODE:-未知}"

for KEY in ENERSIGHT_DATABASE_URL ENERSIGHT_JWT_SECRET; do
  grep -Eq "^${KEY}=.+" .env || fail ".env 缺少 ${KEY}"
done
grep -Eq '^ENERSIGHT_DEBUG=false$' .env || fail "生产必须 ENERSIGHT_DEBUG=false"
if grep -Ev '^[[:space:]]*(#|$)' .env | grep -Eq '<[^>]+>'; then
  fail ".env 的有效配置中仍有占位符"
fi
grep -Eq '^ENERSIGHT_DATABASE_URL=postgresql' .env || fail "生产数据库必须是 PostgreSQL"
if grep -Eq '^ENERSIGHT_WX_APPID=$' .env; then
  echo "预检警告：未配置 ENERSIGHT_WX_APPID，登录会走不通（debug=false 下没有开发态放行）" >&2
fi

# Compose 里的镜像占位符没替换就不能部署
if grep -Eq '<[^>]+>' docker-compose.yml && [ -z "${ENERSIGHT_IMAGE:-}" ]; then
  fail "docker-compose.yml 的镜像还是占位符，先把固定版本与摘要写进去"
fi

if command -v ss >/dev/null 2>&1; then
  if ss -lntH 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:8001[[:space:]]'; then
    CURRENT=$(docker compose ps -q api 2>/dev/null || true)
    [ -n "$CURRENT" ] || fail "宿主机 8001 端口已被其他进程占用"
    echo "预检提示：8001 端口由当前 EnerSight 容器占用，将执行原地升级。"
  fi
fi

mkdir -p data
docker compose config --quiet
echo "预检通过：Compose 配置有效，敏感值未输出。"
