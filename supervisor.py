"""外层看守：循环运行 agent，每轮 git 提交；agent 把自己改坏了就回滚代码。
agent 修改了自己（prompt、工具、主循环、技能）或本轮失败时，推送到人类微信；每 REVIEW_EVERY 轮调用 review.py 复盘。
用法：python supervisor.py [轮数，默认 1] [每轮间隔秒数，默认 60]
agent 不能修改本文件。"""
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CODE = ["agent/loop.py", "agent/tools.py", "agent/prompt.md"]
WATCH = CODE + ["agent/skills"]
SESSIONS = ROOT / "logs" / "sessions.jsonl"


def secrets():
    return {k.strip(): v.strip() for k, v in (l.split("=", 1) for l in (ROOT / "secrets.env").read_text().splitlines()
                                              if "=" in l and not l.startswith("#"))}


def git(*args):
    cmd = ["git", "-c", "user.name=agent", "-c", "user.email=agent@local", *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def commit(msg):
    git("add", "-A")
    git("commit", "-qm", msg)


def check():
    """返回自检的错误信息，通过则返回空字符串。"""
    if "{review}" not in (ROOT / "agent" / "prompt.md").read_text(encoding="utf-8"):
        return "自检不通过：prompt.md 里的 {review} 占位符被删除了，外部复盘必须保留。"
    r = subprocess.run([sys.executable, "agent/loop.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    return "" if r.returncode == 0 else f"自检 loop.py --check 不通过：\n{(r.stdout + r.stderr)[-1500:]}"


def record_failure(text):
    """把失败写进本轮总结，下一轮 agent 会在"最近几轮"里看到。"""
    row = {"time": time.strftime("%m-%d %H:%M"), "summary": f"【本轮失败，由看守程序记录】{text}"}
    with open(SESSIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def notify(title, body):
    print(f"\n[通知] {title}")
    if key := secrets().get("SERVERCHAN_KEY"):
        try:
            requests.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title[:32], "desp": body}, timeout=10)
        except requests.RequestException as e:
            print(f"推送失败：{e}")


def sessions():
    text = SESSIONS.read_text(encoding="utf-8") if SESSIONS.exists() else ""
    return [json.loads(l) for l in text.splitlines() if l]


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    review_every = int(secrets().get("REVIEW_EVERY", 5))

    if not (ROOT / ".git").exists():
        git("init", "-q")
        commit("init")
    good = git("rev-parse", "HEAD")
    (ROOT / "logs").mkdir(exist_ok=True)

    for i in range(1, rounds + 1):
        print(f"\n========== 第 {i}/{rounds} 轮 ==========")
        try:
            r = subprocess.run([sys.executable, "agent/loop.py"], cwd=ROOT, timeout=3600, stderr=subprocess.PIPE, text=True)
            code, error = r.returncode, r.stderr[-1500:]
            sys.stderr.write(r.stderr)
        except subprocess.TimeoutExpired:
            code, error = -1, "运行超过 1 小时，被强制结束。可能卡在某个命令上，或者陷入了死循环。"
        if code not in (0, 3):
            error = f"退出码 {code}，报错：\n{error.strip()}"
        else:
            error = check()

        if not error:
            prev = good
            commit(f"round {i}")
            good = git("rev-parse", "HEAD")
            files = git("diff", "--name-only", prev, good, "--", *WATCH).split()
            if files:
                diff = git("diff", prev, good, "--", *WATCH)
                notify(f"第{i}轮自我修改：{','.join(Path(f).name for f in files)}",
                       f"**本轮总结**：{sessions()[-1]['summary']}\n\n```diff\n{diff[:4000]}\n```")
        else:
            broken = git("diff", "--name-only", good, "--", *CODE).split()
            print(f"本轮失败，代码回滚到 {good[:8]}")
            git("checkout", good, "--", *CODE)
            reverted = (f"你本轮对 {', '.join(broken)} 的修改已被撤销；如果 memory 里记录了这些修改，请更正。"
                        if broken else "代码没有被修改，失败不是改代码导致的。")
            record_failure(f"{error}\n{reverted}")
            commit(f"round {i} rollback")
            notify(f"第{i}轮失败，已回滚", f"{reverted}\n\n```\n{error}\n```")

        if len(sessions()) % review_every == 0:
            print("\n---------- 定期复盘 ----------")
            try:
                subprocess.run([sys.executable, "review.py", str(review_every)], cwd=ROOT, timeout=600)
            except subprocess.TimeoutExpired:
                print("复盘超时，跳过")
            commit(f"review after round {i}")

        if code == 3:
            print("今日 token 预算耗尽，停止")
            break
        if i < rounds:
            time.sleep(interval)


if __name__ == "__main__":
    main()
