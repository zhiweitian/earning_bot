#!/bin/bash
# 停止 supervisor 和正在运行的 agent
cd "$(dirname "$0")"
pid=$(cat logs/supervisor.pid 2>/dev/null)
if [ -n "$pid" ] && kill -- -"$pid" 2>/dev/null; then
    echo "已停止（pid $pid）"
else
    echo "没有在运行"
fi
rm -f logs/supervisor.pid
