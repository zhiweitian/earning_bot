#!/bin/bash
# 后台启动 agent 和微信桥。用法：./start.sh [轮数，默认一直运行]
cd "$(dirname "$0")"
mkdir -p logs
if [ -f logs/supervisor.pid ] && kill -0 "$(cat logs/supervisor.pid)" 2>/dev/null; then
    echo "已经在运行（pid $(cat logs/supervisor.pid)），先运行 ./stop.sh"
    exit 1
fi
if grep -q '^AGENT_USER=' secrets.env && ! id -nG | grep -qw earning; then
    echo "你还不在 earning 组里：请注销重新登录一次，或者先运行 newgrp earning"
    exit 1
fi
umask 002
setsid nohup python3 -u supervisor.py "$@" >> logs/supervisor.out 2>&1 &
echo $! > logs/supervisor.pid
setsid nohup python3 -u wechat.py >> logs/wechat.out 2>&1 &
echo $! > logs/wechat.pid
echo "已启动"
echo "查看 agent：tail -f logs/supervisor.out"
echo "查看微信桥：tail -f logs/wechat.out"
echo "停止运行：./stop.sh"
