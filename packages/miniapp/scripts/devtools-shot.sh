#!/usr/bin/env bash
# 截微信开发者工具里的模拟器画面。
# 用法：scripts/devtools-shot.sh [输出路径]
# 前提：终端已授予「屏幕录制」权限，开发者工具已打开项目。
set -euo pipefail
OUT="${1:-/tmp/enersight-devtools.png}"

# 取工具窗口位置（逻辑像素）
read -r X Y W H < <(osascript -e '
  tell application "System Events" to tell process "wechatdevtools"
    set p to position of first window
    set s to size of first window
    return (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
  end tell' 2>/dev/null | tr ',' ' ')

[ -z "${X:-}" ] && { echo "找不到开发者工具窗口"; exit 1; }

# 模拟器（iPhone 12 机型）在窗口左侧，按窗口尺寸比例定位
SX=$(python3 -c "print(int($X + $W*0.027))")
SY=$(python3 -c "print(int($Y + $H*0.10))")
SW=$(python3 -c "print(int($W*0.235))")
SH=$(python3 -c "print(int($H*0.74))")

screencapture -x -o -R "$SX,$SY,$SW,$SH" "$OUT"
echo "→ $OUT"
