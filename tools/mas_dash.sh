#!/usr/bin/env bash
# MAS 终端看板（本地一键）——上传最新脚本到 AUTO-MAS 主机并运行。
#
# 用法:
#   bash tools/mas_dash.sh                    # 今日 + 近 7 天
#   bash tools/mas_dash.sh --days 3 --top 20
#   bash tools/mas_dash.sh --json > dash.json
#
# 可用环境变量覆盖连接信息:
#   MAS_SSH_KEY / MAS_HOST
set -u

KEY="${MAS_SSH_KEY:-C:\\Users\\xiach\\.ssh\\maaorch_target}"
HOST="${MAS_HOST:-Administrator@100.79.173.69}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/mas_dashboard.py"

if [ ! -f "$SRC" ]; then
  echo "找不到脚本: $SRC" >&2
  exit 1
fi

ARGS="$*"
B64="$(base64 -w0 "$SRC")"

printf '%s' "$B64" | ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST" \
  "set \"PYTHONIOENCODING=utf-8\" && python -c \"import sys,base64; open(r'E:\\\\mas_dashboard.py','wb').write(base64.b64decode(sys.stdin.read()))\" && python E:\\mas_dashboard.py $ARGS"
