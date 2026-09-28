#!/bin/bash
# _baseline_catch.sh v2 —— 轮询 l-1 gui.log：计数「已开始探索/肉鸽结算/战斗失败」(ASCII，免编码问题)
# 计数器变化(新局开始/结算被读/战斗失败)时立即截屏取证。只读：尾读 + adb 截屏。
# 截图存 runtime/fleet_cases（rogue_fleet_probe --shot 默认位置）。
# 用法: bash tools/_baseline_catch.sh <poll数> [间隔秒]
set -u
KEY="$HOME/.ssh/maaorch_target"
HOST="Administrator@100.79.173.69"
CNT_CMD='powershell -NoProfile -Command "$c=Get-Content -Raw -Encoding UTF8 \"E:\maa-alas\fz-maa\l\l-1\debug\gui.log\"; $e=([regex]::Matches($c,\"\u5df2\u5f00\u59cb\u63a2\u7d22\")).Count; $s=([regex]::Matches($c,\"\u8089\u9e3d\u7ed3\u7b97\")).Count; $f=([regex]::Matches($c,\"\u6218\u6597\u5931\u8d25\")).Count; \"exp=$e set=$s fail=$f\""'
TAIL_CMD='powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 4 \"E:\maa-alas\fz-maa\l\l-1\debug\gui.log\""'
N="${1:-22}"
GAP="${2:-8}"
prev=""
for i in $(seq 1 "$N"); do
  counts=$(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no "$HOST" "$CNT_CMD" 2>/dev/null | tr -d '\r\n ')
  tailtxt=$(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no "$HOST" "$TAIL_CMD" 2>/dev/null | tr -d '\r')
  task=$(printf '%s\n' "$tailtxt" | tail -1)
  echo "[$(date +%H:%M:%S)] poll#$i $counts | $task"
  if [ -n "$prev" ] && [ -n "$counts" ] && [ "$counts" != "$prev" ]; then
    echo "[$(date +%H:%M:%S)] == COUNTER CHANGE: $prev -> $counts =="
    cd /d/1-2/MAAOrch || exit 1
    node tools/rogue_fleet_probe.mjs --shot l-1 2>&1 | tail -1
    echo "[$(date +%H:%M:%S)] shot done"
  fi
  prev="$counts"
  sleep "$GAP"
done
echo "loop exit"
