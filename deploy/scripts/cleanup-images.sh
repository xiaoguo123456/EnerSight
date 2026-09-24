#!/usr/bin/env bash
# 仅清理本服务已失去标签的旧镜像；当前与上一健康版本保留在本机供回滚。
set -euo pipefail
cd "$(dirname "$0")/.."

repository=${1:?用法：cleanup-images.sh 镜像仓库地址}
[[ "$repository" == */enersight-backend ]] || { echo '仅允许清理 EnerSight 镜像' >&2; exit 1; }

protected=()
for file in .release.env .previous-release.env; do
  [[ -f "$file" ]] || continue
  image=$(sed -n 's/^ENERSIGHT_IMAGE=//p' "$file")
  [[ -n "$image" ]] || { echo "$file 缺少镜像标识，跳过清理" >&2; exit 1; }
  id=$(docker image inspect "$image" --format '{{.Id}}') || {
    echo "$file 的镜像不在本机，跳过清理" >&2
    exit 1
  }
  protected+=("$id")
done

images=$(docker image ls "$repository" --filter dangling=true --no-trunc --format '{{.ID}}')
removed=0
while IFS= read -r id; do
  [[ -n "$id" ]] || continue
  keep=false
  for saved in "${protected[@]}"; do
    if [[ "$id" == "$saved" ]]; then keep=true; break; fi
  done
  if [[ "$keep" == true ]]; then continue; fi
  docker image rm "$id" >/dev/null
  ((removed += 1))
done <<< "$images"
echo "已清理 EnerSight 旧镜像：$removed 个"
