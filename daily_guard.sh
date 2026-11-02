#!/usr/bin/env bash
# 每日守卫（Windows 计划任务 09:20 / 15:10 调）：
# 1) 刷新本地数据（E:\market-watch\index.html 用）
# 2) 检查线上 Pages 数据是否过期，过期就 dispatch 触发 CI（机器开机时兜底）
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$DIR/update.sh"
# 线上是每天 23:10 一次，所以只有超过 26 小时才补触发
MAX_AGE=93600 bash "$DIR/remote_watchdog.sh"
