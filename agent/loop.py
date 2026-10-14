import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

from openai import OpenAI

import tools

ROOT = tools.ROOT
AGENT = ROOT / "agent"
LOGS = ROOT / "logs"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MAX_STEPS = int(os.getenv("MAX_STEPS", 40))
DAILY_TOKENS = int(os.getenv("DAILY_TOKENS", 2_000_000))
OUT_LIMIT = 6000
CONTEXT_CHARS = 200_000


def read(p):
    return p.read_text(encoding="utf-8") if p.exists() else ""


def tokens_today():
    return json.loads(read(LOGS / "usage.json") or "{}").get(str(date.today()), 0)


def add_usage(n):
    path = LOGS / "usage.json"
    usage = json.loads(read(path) or "{}")
    usage[str(date.today())] = usage.get(str(date.today()), 0) + n
    path.write_text(json.dumps(usage))


def skills_list():
    lines = []
    for p in sorted((AGENT / "skills").glob("*")):
        if p.is_file():
            first = (read(p).strip().splitlines() or [""])[0].lstrip("#\"' ")
            lines.append(f"- agent/skills/{p.name}: {first}")
    return "\n".join(lines) or "（暂无）"


def recent_sessions(n=5):
    rows = [json.loads(l) for l in read(LOGS / "sessions.jsonl").splitlines() if l]
    return "\n".join(f"- [{r['time']}] {r['summary']}" for r in rows[-n:]) or "（这是第一轮）"


def build_system():
    s = read(AGENT / "prompt.md")
    for k, v in {
        "{time}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{goal}": read(AGENT / "goal.md"),
        "{memory}": read(AGENT / "memory.md") or "（空）",
        "{skills}": skills_list(),
        "{recent}": recent_sessions(),
        "{max_steps}": str(MAX_STEPS),
    }.items():
        s = s.replace(k, v)
    return s


def compact(messages):
    """上下文太长时，把较早的工具输出替换成占位符，保留最近 10 条消息。"""
    if sum(len(str(m.get("content") or "")) for m in messages) < CONTEXT_CHARS:
        return
    for m in messages[:-10]:
        if m["role"] == "tool":
            m["content"] = "（早期输出已省略）"


def run():
    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    messages = [
        {"role": "system", "content": build_system()},
        {"role": "user", "content": "新一轮开始。"},
    ]
    log = open(LOGS / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl", "a", encoding="utf-8")
    summary = None

    for step in range(MAX_STEPS + 3):
        if tokens_today() > DAILY_TOKENS:
            summary = summary or "（今日 token 预算耗尽，被中断）"
            break
        if step == MAX_STEPS:
            messages.append({"role": "user", "content": "步数已用完，立即更新 memory 并调用 finish。"})

        compact(messages)
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=tools.SCHEMAS)
        add_usage(resp.usage.total_tokens)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        log.write(json.dumps(messages[-1], ensure_ascii=False) + "\n")
        if msg.content:
            print(f"\n[{step}] {msg.content}")

        if not msg.tool_calls:
            messages.append({"role": "user", "content": "继续。本轮完成了就调用 finish。"})
            continue

        for tc in msg.tool_calls:
            name, args = tc.function.name, tc.function.arguments
            print(f"  → {name} {args[:200]}")
            out = tools.call(name, args)
            if name == "finish":
                summary = json.loads(args).get("summary", "")
            if len(out) > OUT_LIMIT:
                out = out[:OUT_LIMIT] + f"\n...（已截断，共 {len(out)} 字符）"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
            log.write(json.dumps(messages[-1], ensure_ascii=False) + "\n")
        if summary is not None:
            break

    with open(LOGS / "sessions.jsonl", "a", encoding="utf-8") as f:
        row = {"time": datetime.now().strftime("%m-%d %H:%M"), "summary": summary or "（未总结）"}
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\n本轮结束：{summary}")
    if tokens_today() > DAILY_TOKENS:
        sys.exit(3)


if __name__ == "__main__":
    LOGS.mkdir(exist_ok=True)
    tools.WORKSPACE.mkdir(exist_ok=True)
    (AGENT / "skills").mkdir(exist_ok=True)
    if "--check" in sys.argv:
        assert "{goal}" in read(AGENT / "prompt.md") and "{memory}" in read(AGENT / "prompt.md")
        build_system()
        assert {"shell", "finish", "ask_human"} <= set(tools.TOOLS)
        print("check ok")
    else:
        run()
