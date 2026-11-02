#!/usr/bin/env bash
# 在线版看门狗：GitHub Pages 的 data.js 超过 MAX_AGE 秒未更新时，
# 用 repository_dispatch 手动触发 CI 刷新（GitHub schedule 偶有延迟时的兜底）。
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/data/watchdog.log"
URL="https://hana-ame.github.io/market-watch/data.js"
REPO="Hana-ame/market-watch"
MAX_AGE="${MAX_AGE:-1800}"
GH="${GH:-/usr/bin/gh}"

mkdir -p "$DIR/data"
stamp="$(date '+%F %T')"
pages="$(curl -s --max-time 20 "$URL")"
ts="$(printf '%s' "$pages" | grep -o '"generated_ts":[0-9]*' | head -1 | cut -d: -f2)"
now="$(date +%s)"

if [ -z "${ts:-}" ]; then
  msg="$stamp 拉取 Pages 失败，尝试触发 CI"
  "$GH" api -X POST "repos/$REPO/dispatches" -f event_type=refresh >/dev/null 2>&1 \
    && msg="$msg ... ok" || msg="$msg ... failed"
else
  age=$(( now - ts ))
  if [ "$age" -gt "$MAX_AGE" ]; then
    msg="$stamp 线上数据 ${age}s 未更新（阈值 ${MAX_AGE}s），触发 CI"
    "$GH" api -X POST "repos/$REPO/dispatches" -f event_type=refresh >/dev/null 2>&1 \
      && msg="$msg ... ok" || msg="$msg ... failed"
  else
    msg="$stamp 线上数据 ${age}s 前更新，跳过"
  fi
fi
printf '%s\n' "$msg" >>"$LOG"
tail -n 300 "$LOG" >"$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit 0
