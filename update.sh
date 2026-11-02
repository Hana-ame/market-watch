#!/usr/bin/env bash
# 每日看盘 · 单次刷新（cron / Windows 计划任务 / 手动共用）
# 用法：bash /mnt/e/market-watch/update.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/data/update.log"
LOCK="$DIR/.update.lock"

mkdir -p "$DIR/data"
export MARKET_HISTORY_PATH="$DIR/data/history.local.json"   # 本地历史与仓库历史分开，避免弄脏 git 工作区
export MARKET_FALLBACK_PATH="$DIR/data/fallback.local.json"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[skip] $(date '+%F %T') 已有刷新在跑" >>"$LOG"
  exit 0
fi

start=$(date +%s)
printf '\n[run ] %s\n' "$(date '+%F %T')" >>"$LOG"
python3 "$DIR/fetch_market.py" >>"$LOG" 2>&1
rc=$?
printf '[done] rc=%s %ss\n' "$rc" "$(( $(date +%s) - start ))" >>"$LOG"

# 日志只留最近 600 行
if [ -f "$LOG" ]; then
  tail -n 600 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exit $rc