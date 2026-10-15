#!/bin/bash
# 后台启动 agent。用法：./start.sh [轮数，默认一直运行]
cd "$(dirname "$0")"
mkdir -p logs
if [ -f logs/supervisor.pid ] && kill -0 "$(cat logs/supervisor.pid)" 2>/dev/null; then
    echo "已经在运行（pid $(cat logs/supervisor.pid)）"
    exit 1
fi
setsid nohup python3 -u supervisor.py "$@" >> logs/supervisor.out 2>&1 &
echo $! > logs/supervisor.pid
echo "已启动（pid $!）"
echo "查看输出：tail -f logs/supervisor.out"
echo "停止运行：./stop.sh"
