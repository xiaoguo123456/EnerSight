#!/usr/bin/env bash
# 用假的 Docker 验证清理范围与回滚保护，不操作真实镜像。
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -r "$tmp"' EXIT
mkdir -p "$tmp/deploy/scripts" "$tmp/bin"
cp "$root/deploy/scripts/cleanup-images.sh" "$tmp/deploy/scripts/"

repository=registry.example/weishen/enersight-backend
printf 'ENERSIGHT_IMAGE=%s:current@sha256:current\n' "$repository" > "$tmp/deploy/.release.env"
printf 'ENERSIGHT_IMAGE=%s:previous@sha256:previous\n' "$repository" > "$tmp/deploy/.previous-release.env"
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
  'image inspect')
    case "$3" in
      *:current@*) echo sha256:current ;;
      *:previous@*)
        [[ "${MOCK_MISSING_PREVIOUS:-}" != 1 ]] || exit 1
        echo sha256:previous ;;
      *) exit 1 ;;
    esac ;;
  'image ls')
    [[ "$3" == "$MOCK_REPOSITORY" ]] || exit 1
    [[ "${MOCK_FAIL_LIST:-}" != 1 ]] || exit 1
    printf '%s\n' sha256:current sha256:previous sha256:old ;;
  'image rm')
    [[ "${MOCK_FAIL_RM:-}" != 1 ]] || exit 1
    printf '%s\n' "$3" >> "$MOCK_REMOVED" ;;
  *) exit 1 ;;
esac
SH
chmod +x "$tmp/bin/docker"
export PATH="$tmp/bin:$PATH" MOCK_REPOSITORY="$repository" MOCK_REMOVED="$tmp/removed"
bash "$tmp/deploy/scripts/cleanup-images.sh" "$repository" > "$tmp/output"
[[ "$(cat "$tmp/removed")" == sha256:old ]]
[[ "$(cat "$tmp/output")" == '已清理 EnerSight 旧镜像：1 个' ]]

: > "$tmp/removed"
if MOCK_MISSING_PREVIOUS=1 bash "$tmp/deploy/scripts/cleanup-images.sh" "$repository" > /dev/null 2>&1; then
  echo '回滚镜像无法识别时必须停止清理' >&2; exit 1
fi
[[ ! -s "$tmp/removed" ]]
if MOCK_FAIL_LIST=1 bash "$tmp/deploy/scripts/cleanup-images.sh" "$repository" > /dev/null 2>&1; then
  echo 'Docker 查询失败时必须报告错误' >&2; exit 1
fi
if MOCK_FAIL_RM=1 bash "$tmp/deploy/scripts/cleanup-images.sh" "$repository" > /dev/null 2>&1; then
  echo 'Docker 删除失败时必须报告错误' >&2; exit 1
fi
if bash "$tmp/deploy/scripts/cleanup-images.sh" registry.example/other-service > /dev/null 2>&1; then
  echo '不能清理其他服务镜像' >&2; exit 1
fi
echo '镜像清理范围与回滚保护测试通过'
