#!/usr/bin/env bash
# 仅更新当前部署目录中的 API；失败恢复上一镜像，不回退数据库迁移。
set -Eeuo pipefail
cd "$(dirname "$0")/.."
exec 9>.deploy.lock
flock -n 9 || { echo '已有部署正在运行'; exit 1; }
IMAGE=${1:?用法：deploy.sh 镜像完整摘要}
[[ "$IMAGE" =~ /enersight-backend:[a-f0-9]{12}@sha256:[a-f0-9]{64}$ ]] || {
  echo '镜像必须包含 12 位提交标签和 SHA256 摘要'; exit 1;
}
export ENERSIGHT_IMAGE="$IMAGE"
export ENERSIGHT_DEPLOY_ENV=${2:-prod}
case "$ENERSIGHT_DEPLOY_ENV" in
  prod) export ENERSIGHT_DEPLOY_PORT=8001 ;;
  test) export ENERSIGHT_DEPLOY_PORT=8002 ;;
  *) echo "未知部署环境"; exit 1 ;;
esac
bash scripts/preflight.sh
# 清理上次发布留下的旧镜像，防止磁盘满时连新镜像都拉不下来。
repository=${IMAGE%@sha256:*}
repository=${repository%:*}
if ! bash scripts/cleanup-images.sh "$repository"; then
  echo '旧镜像清理失败，继续发布；请检查磁盘空间。' >&2
fi
# 拉取成功后再保存版本和替换服务。
docker compose pull api
PREVIOUS=''
if [[ -f .release.env ]]; then
  PREVIOUS=$(sed -n 's/^ENERSIGHT_IMAGE=//p' .release.env)
fi
wait_ready() {
  local attempt cid state
  for attempt in $(seq 1 60); do
    cid=$(docker compose ps -aq api)
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || true)
    if [[ "$state" == healthy ]] && curl --max-time 5 -fsS http://127.0.0.1:${ENERSIGHT_DEPLOY_PORT}/ready >/dev/null; then
      return 0
    fi
    sleep 5
  done
  return 1
}
if docker compose up -d --no-deps api && wait_ready; then
  [[ ! -f .release.env ]] || cp .release.env .previous-release.env
  printf 'ENERSIGHT_IMAGE=%s\n' "$IMAGE" > .release.env
  chmod 600 .release.env
  echo "发布成功：$IMAGE"
  if ! bash scripts/cleanup-images.sh "$repository"; then
    echo '发布成功，但旧镜像清理失败；请检查磁盘空间。' >&2
  fi
else
  echo '发布未通过就绪检查。数据库迁移不会自动回退。' >&2
  if [[ -n "$PREVIOUS" ]]; then
    export ENERSIGHT_IMAGE="$PREVIOUS"
    docker compose up -d --no-deps api
    wait_ready && echo '已恢复上一健康镜像。' || echo '回滚仍不健康，需要人工排查。' >&2
  else
    echo '首次部署无可回滚镜像，保留容器供排查。' >&2
  fi
  exit 1
fi
