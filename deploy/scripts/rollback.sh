#!/usr/bin/env bash
# 回滚应用镜像，不回退数据库；也可显式传入历史镜像摘要。
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${1:-$(sed -n 's/^ENERSIGHT_IMAGE=//p' .previous-release.env)}
exec bash scripts/deploy.sh "$IMAGE" "${2:-prod}"
