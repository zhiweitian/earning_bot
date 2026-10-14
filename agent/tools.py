import inspect
import json
import os
import re
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
PROTECTED = {ROOT / "supervisor.py", ROOT / "agent" / "goal.md"}

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


@tool
def ask_human(question: str):
    """向人类提问或请求批准。任何对外操作（发帖、发消息、注册、付款、提交给客户）之前必须调用。"""
    if os.getenv("HUMAN", "1") == "0":
        with open(WORKSPACE / "requests.md", "a", encoding="utf-8") as f:
            f.write(f"- {question}\n")
        return "人类当前不在。请求已记录到 workspace/requests.md，先去做不需要批准的事。"
    print(f"\n[agent 提问] {question}")
    return input("你的回答> ")


@tool
def finish(summary: str):
    """结束本轮工作。summary 写清：做了什么、结果如何、下一轮最该做什么。"""
    return "ok"


def call(name, arguments):
    try:
        return str(TOOLS[name](**json.loads(arguments or "{}")))
    except Exception as e:
        return f"错误：{type(e).__name__}: {e}"
