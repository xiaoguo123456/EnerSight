#!/usr/bin/env bash
# 在模拟器上点击。坐标为模拟器截图（scripts/devtools-shot.sh 产出）里的像素位置。
# 用法：scripts/devtools-tap.sh <x> <y>
set -euo pipefail
PX=$1; PY=$2
read -r X Y W H < <(osascript -e '
  tell application "System Events" to tell process "wechatdevtools"
    set p to position of first window
    set s to size of first window
    return (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
  end tell' 2>/dev/null | tr ',' ' ')
# 截图是 2x，先除 2 得逻辑坐标，再加上模拟器区域偏移
SX=$(python3 -c "print(int($X + $W*0.027 + $PX/2))")
SY=$(python3 -c "print(int($Y + $H*0.10 + $PY/2))")
osascript -e "tell application \"System Events\" to click at {$SX, $SY}"
echo "点击 ($SX, $SY)"
