#!/usr/bin/env sh
# 回滚到上一个已验证镜像。只回滚应用，不回退数据库迁移。
set -eu

if [ "$#" -ne 1 ]; then
  echo "用法：$0 <ACR 地址>/enersight/api:<上一固定版本>@sha256:<摘要>" >&2
  exit 1
fi

case "$1" in
  *:latest|*:latest@*)
    echo "拒绝回滚：不允许使用会漂移的 latest 标签。" >&2
    exit 1
    ;;
  */enersight/api:?*|*/enersight/api@sha256:*) ;;
  *)
    echo "拒绝回滚：镜像必须是 enersight/api 且指定标签或摘要。" >&2
    exit 1
    ;;
esac

ENERSIGHT_IMAGE=$1
export ENERSIGHT_IMAGE

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
"$SCRIPT_DIR/deploy.sh"

echo "已临时回滚到 $ENERSIGHT_IMAGE。请随后把该版本写入 docker-compose.yml 并提交记录。"
