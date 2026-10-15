"""外层看守：循环运行 agent，每轮 git 提交；agent 把自己改坏了就回滚代码。
agent 修改了自己（prompt、工具、主循环、技能）或本轮失败时，推送到人类微信；每 REVIEW_EVERY 轮调用 review.py 复盘。
用法：python supervisor.py [轮数，默认 0 表示一直运行]
每轮之间的休眠时间由 agent 在 finish 里决定；人类答复 requests.md 会提前唤醒。
每天的花费按 DeepSeek 账户余额的实际减少计算，超过 DAILY_BUDGET_CNY 就休眠到第二天。
配置了 AGENT_USER 时，agent 以这个 Linux 用户的身份运行（见 setup_agent_user.sh）。
agent 不能修改本文件。"""
import itertools
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from notify import ROOT, STATE, notify, secrets

CODE = ["agent/loop.py", "agent/tools.py", "agent/prompt.md"]
WATCH = CODE + ["agent/skills"]
SESSIONS = ROOT / "logs" / "sessions.jsonl"


def agent_cmd(*args):
    """以 agent 用户运行 agent 的代码；没配置独立用户时直接用当前 Python。"""
    user = secrets().get("AGENT_USER")
    if not user:
        return [sys.executable, *args]
    return ["sudo", "-H", "-u", user, "/bin/sh", "-c", 'umask 002 && exec "$0" "$@"', str(ROOT / ".venv/bin/python"), *args]


def spent_today():
    """返回 (今天已花的钱, 当前余额)，单位元。以每天第一次查询时的余额为起点；充值后起点重置。"""
    r = requests.get("https://api.deepseek.com/user/balance", timeout=15,
                     headers={"Authorization": f"Bearer {secrets()['DEEPSEEK_API_KEY']}"})
    balance = float(r.json()["balance_infos"][0]["total_balance"])
    path = STATE / "budget.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    if state.get("date") != str(date.today()) or balance > state["start"]:
        state = {"date": str(date.today()), "start": balance}
        STATE.mkdir(mode=0o700, exist_ok=True)
        path.write_text(json.dumps(state))
    return state["start"] - balance, balance


def sleep_until_tomorrow():
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=5, second=0)
    sleep((tomorrow - datetime.now()).total_seconds() / 60)


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
    r = subprocess.run(agent_cmd("agent/loop.py", "--check"), cwd=ROOT, capture_output=True, text=True)
    return "" if r.returncode == 0 else f"自检 loop.py --check 不通过：\n{(r.stdout + r.stderr)[-1500:]}"


def record_failure(text):
    """把失败写进本轮总结，下一轮 agent 会在"最近几轮"里看到。"""
    row = {"time": time.strftime("%m-%d %H:%M"), "summary": f"【本轮失败，由看守程序记录】{text}"}
    with open(SESSIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sessions():
    text = SESSIONS.read_text(encoding="utf-8") if SESSIONS.exists() else ""
    return [json.loads(l) for l in text.splitlines() if l]


def sleep(minutes):
    """休眠，期间人类修改了 requests.md 就提前醒来。"""
    requests_file = ROOT / "workspace" / "requests.md"
    mtime = lambda: requests_file.stat().st_mtime if requests_file.exists() else 0
    start, end = mtime(), time.time() + minutes * 60
    print(f"\n休眠 {minutes:.0f} 分钟，到 {datetime.fromtimestamp(end):%H:%M}（答复 requests.md 会提前唤醒）")
    while time.time() < end:
        time.sleep(30)
        if mtime() != start:
            print("检测到人类答复，提前唤醒")
            return


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    review_every = int(secrets().get("REVIEW_EVERY", 5))
    failures = 0

    if not (ROOT / ".git").exists():
        git("init", "-q")
        commit("init")
    good = git("rev-parse", "HEAD")
    (ROOT / "logs").mkdir(exist_ok=True)

    budget = float(secrets().get("DAILY_BUDGET_CNY", 5))
    for i in itertools.count(1):
        try:
            spent, balance = spent_today()
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"查询 DeepSeek 余额失败：{e}")
            spent, balance = 0, None
        if spent >= budget:
            notify("今日预算已用完", f"今天已花 {spent:.2f} 元（上限 {budget} 元），余额 {balance:.2f} 元。休眠到明天。")
            sleep_until_tomorrow()
            continue

        print(f"\n========== 第 {i} 轮 {datetime.now():%m-%d %H:%M}  今日已花 {spent:.2f}/{budget} 元 ==========")
        try:
            r = subprocess.run(agent_cmd("agent/loop.py"), cwd=ROOT, timeout=3600, stderr=subprocess.PIPE, text=True)
            code, error = r.returncode, r.stderr[-1500:]
            sys.stderr.write(r.stderr)
        except subprocess.TimeoutExpired:
            code, error = -1, "运行超过 1 小时，被强制结束。可能卡在某个命令上，或者陷入了死循环。"
        if code not in (0, 3):
            error = f"退出码 {code}，报错：\n{error.strip()}"
        else:
            error = check()

        if not error:
            failures = 0
            prev = good
            commit(f"round {i}")
            good = git("rev-parse", "HEAD")
            files = git("diff", "--name-only", prev, good, "--", *WATCH).split()
            if files:
                diff = git("diff", prev, good, "--", *WATCH)
                notify(f"第{i}轮自我修改：{','.join(Path(f).name for f in files)}",
                       f"**本轮总结**：{sessions()[-1]['summary']}\n\n```diff\n{diff[:4000]}\n```")
        else:
            failures += 1
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

        if failures >= 3:
            notify("连续失败 3 次，已停止运行", "请检查网络、API 余额和 logs/supervisor.out，处理好后重新运行 ./start.sh")
            break
        if i == rounds:
            break
        if code == 3:
            print("今日 token 上限已用完")
            sleep_until_tomorrow()
        elif error:
            sleep(5)
        else:
            sleep(min(max(sessions()[-1].get("sleep") or 10, 1), 240))


if __name__ == "__main__":
    main()
