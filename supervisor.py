"""外层看守：循环运行 agent，每轮 git 提交；agent 把自己改坏了就回滚代码。
用法：python supervisor.py [轮数，默认 1] [每轮间隔秒数，默认 60]
agent 不能修改本文件。"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 60
CODE = ["agent/loop.py", "agent/tools.py", "agent/prompt.md"]


def git(*args):
    cmd = ["git", "-c", "user.name=agent", "-c", "user.email=agent@local", *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def commit(msg):
    git("add", "-A")
    git("commit", "-qm", msg)


def healthy():
    return subprocess.run([sys.executable, "agent/loop.py", "--check"], cwd=ROOT).returncode == 0


if not (ROOT / ".git").exists():
    git("init", "-q")
    commit("init")
good = git("rev-parse", "HEAD")

for i in range(1, ROUNDS + 1):
    print(f"\n========== 第 {i}/{ROUNDS} 轮 ==========")
    try:
        code = subprocess.run([sys.executable, "agent/loop.py"], cwd=ROOT, timeout=3600).returncode
    except subprocess.TimeoutExpired:
        code = -1

    if code in (0, 3) and healthy():
        commit(f"round {i}")
        good = git("rev-parse", "HEAD")
    else:
        print(f"本轮失败（exit {code}）或自检不通过，代码回滚到 {good[:8]}")
        git("checkout", good, "--", *CODE)
        commit(f"round {i} rollback")

    if code == 3:
        print("今日 token 预算耗尽，停止")
        break
    if i < ROUNDS:
        time.sleep(INTERVAL)
