#!/usr/bin/env bash
# 每日守卫（Windows 计划任务 09:20 / 15:10 调）：
# 1) 刷新本地数据（E:\market-watch\index.html 用）
# 2) 检查线上 Pages 数据是否过期，过期就 dispatch 触发 CI（机器开机时兜底）
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$DIR/update.sh"
bash "$DIR/remote_watchdog.sh"
