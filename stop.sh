#!/bin/bash
# 停止 supervisor（连同正在运行的 agent）和微信桥
cd "$(dirname "$0")"
for name in supervisor wechat; do
    pid=$(cat logs/$name.pid 2>/dev/null)
    if [ -n "$pid" ] && kill -- -"$pid" 2>/dev/null; then
        echo "已停止 $name（pid $pid）"
    fi
    rm -f logs/$name.pid
done
