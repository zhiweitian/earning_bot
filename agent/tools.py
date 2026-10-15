import inspect
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
REQUESTS = WORKSPACE / "requests.md"
SECRETS_FILE = ROOT / "secrets.env"
PROTECTED = {ROOT / "supervisor.py", ROOT / "review.py", ROOT / "agent" / "goal.md", ROOT / "agent" / "review.md", SECRETS_FILE}
PUBLIC_CONFIG = {"EXPERT_BASE_URL", "EXPERT_MODELS", "EXPERT_DAILY", "DEEPSEEK_MODEL", "REVIEW_MODEL", "REVIEW_EVERY"}

SECRETS = {}
if SECRETS_FILE.exists():
    for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            SECRETS[k.strip()] = v.strip()
    os.environ.update(SECRETS)

TOOLS, SCHEMAS = {}, []


def tool(fn):
    """注册工具：函数签名自动转成 schema，docstring 作为描述。int 注解的参数为整数，其余为字符串。"""
    props, required = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        props[name] = {"type": "integer" if p.annotation is int else "string"}
        if p.default is p.empty:
            required.append(name)
    TOOLS[fn.__name__] = fn
    SCHEMAS.append({"type": "function", "function": {
        "name": fn.__name__,
        "description": inspect.cleandoc(fn.__doc__ or ""),
        "parameters": {"type": "object", "properties": props, "required": required},
    }})
    return fn


def resolve(path):
    p = Path(path)
    return (p if p.is_absolute() else ROOT / p).resolve()


@tool
def shell(command: str, timeout: int = 120):
    """在 workspace/ 目录下执行 bash 命令，返回退出码和输出。timeout 单位秒。"""
    r = subprocess.run(command, shell=True, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout)
    return f"[exit {r.returncode}]\n{r.stdout}{r.stderr}"


@tool
def read_file(path: str):
    """读取文件。相对路径以项目根目录为准，如 agent/memory.md、workspace/xxx。"""
    return resolve(path).read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str):
    """整体写入文件（覆盖），目录不存在会自动创建。"""
    p = resolve(path)
    if p in PROTECTED:
        return f"拒绝：{path} 受保护，不能修改"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符）"


@tool
def edit_file(path: str, old: str, new: str):
    """把文件中唯一出现的 old 片段替换为 new。适合小改动，old 必须在文件中恰好出现一次。"""
    p = resolve(path)
    if p in PROTECTED:
        return f"拒绝：{path} 受保护，不能修改"
    text = p.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        return f"失败：old 在文件中出现了 {n} 次，需要恰好 1 次"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return f"已修改 {path}"


@tool
def fetch_url(url: str):
    """抓取网页，HTML 会被转成纯文本。搜索可以用 https://cn.bing.com/search?q=关键词"""
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0"})
    if "html" not in r.headers.get("content-type", ""):
        return r.text
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", r.text)
    text = re.sub(r"(?is)<a [^>]*href=\"(http[^\"]+)\"[^>]*>(.*?)</a>", r"\2 [\1]", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def notify(title, body):
    """推送到人类微信：配置了 SERVERCHAN_KEY 用 Server酱，配置了 PUSHPLUS_TOKEN 用 PushPlus。"""
    print(f"\n[通知] {title}\n{body}")
    if key := os.getenv("SERVERCHAN_KEY"):
        requests.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title, "desp": body}, timeout=10)
    elif token := os.getenv("PUSHPLUS_TOKEN"):
        requests.post("https://www.pushplus.plus/send", json={"token": token, "title": title, "content": body}, timeout=10)


@tool
def ask_human(question: str):
    """通过微信向人类提问、请求批准或索要账号。任何对外操作（发帖、私信、注册、付款、提交给客户、上架）之前必须调用。
    不会等待回复：答复会出现在后续轮次的"收件箱"里。多个问题请合并成一次提问。"""
    text = REQUESTS.read_text(encoding="utf-8") if REQUESTS.exists() else ""
    n = len(re.findall(r"^## #", text, re.M)) + 1
    with open(REQUESTS, "a", encoding="utf-8") as f:
        f.write(f"\n## #{n} [待答复] {datetime.now():%m-%d %H:%M}\n问题：{question}\n答复：\n")
    notify(f"#{n} {question[:28]}", f"{question}\n\n请在 workspace/requests.md 的 #{n} 下填写答复。")
    return f"已发到人类微信（请求 #{n}）。不要等待，先做不依赖这个答复的事。"


@tool
def ask_expert(question: str, model: str = ""):
    """遇到靠自己反复做不好的难题（复杂代码、关键决策、方案评审）时，请教更强的模型。它很贵，每天次数有限，简单问题不要用。
    对方看不到你的任何上下文，question 必须自包含：背景、已有尝试、相关代码或数据、你具体想要什么。model 留空用默认模型。"""
    models = [m.strip() for m in os.getenv("EXPERT_MODELS", "").split(",") if m.strip()]
    if not models:
        return "未配置专家模型"
    usage_file = ROOT / "logs" / "usage.json"
    usage = json.loads(usage_file.read_text()) if usage_file.exists() else {}
    key = f"expert-{datetime.now():%Y-%m-%d}"
    if usage.get(key, 0) >= int(os.getenv("EXPERT_DAILY", 10)):
        return "今天的专家调用次数已用完，自己想办法或明天再问"
    usage[key] = usage.get(key, 0) + 1
    usage_file.write_text(json.dumps(usage))
    client = OpenAI(api_key=os.environ["EXPERT_API_KEY"], base_url=os.environ["EXPERT_BASE_URL"])
    r = client.chat.completions.create(model=model or models[0], messages=[{"role": "user", "content": question}], max_tokens=8000)
    return r.choices[0].message.content


@tool
def finish(summary: str, sleep_minutes: int = 10):
    """结束本轮工作。summary 写清：做了什么、结果如何、下一轮最该做什么。
    sleep_minutes 是多久后开始下一轮：手上还有可以马上接着做的事就填 1～5；在等人类答复或外部结果（数据、反馈）就填 60～240，人类一答复你就会被提前唤醒。不要在没事可做时频繁醒来空转。"""
    return "ok"


def call(name, arguments):
    try:
        out = str(TOOLS[name](**json.loads(arguments or "{}")))
    except Exception as e:
        out = f"错误：{type(e).__name__}: {e}"
    for k, v in SECRETS.items():
        if k not in PUBLIC_CONFIG and len(v) >= 6:
            out = out.replace(v, "***")
    return out
