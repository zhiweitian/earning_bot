"""给人类发微信：优先走微信 ClawBot（需要先运行 python wechat.py login，并在微信里给机器人发过消息），不行再用 Server酱。
agent 不能修改本文件。"""
import base64
import json
import os
import struct
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".supervisor"
WECHAT = STATE / "wechat.json"
BASE_INFO = {"channel_version": "earning-1.0"}


def secrets():
    return {k.strip(): v.strip() for k, v in (l.split("=", 1) for l in (ROOT / "secrets.env").read_text().splitlines()
                                              if "=" in l and not l.startswith("#"))}


def wechat_state():
    return json.loads(WECHAT.read_text()) if WECHAT.exists() else {}


def save_wechat_state(state):
    STATE.mkdir(mode=0o700, exist_ok=True)
    tmp = WECHAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(WECHAT)


def ilink(path, body, state, timeout=15):
    headers = {
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {state['token']}",
        "X-WECHAT-UIN": base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode(),
    }
    url = f"{state['base_url'].rstrip('/')}/{path}"
    return requests.post(url, json={**body, "base_info": BASE_INFO}, headers=headers, timeout=timeout).json()


def send_wechat(text, state=None):
    """通过 ClawBot 发消息给人类，成功返回 True。"""
    state = state or wechat_state()
    if not state.get("context_token"):
        return False
    r = ilink("ilink/bot/sendmessage", {"msg": {
        "from_user_id": "", "to_user_id": state["user"], "client_id": f"earning-{os.urandom(8).hex()}",
        "message_type": 2, "message_state": 2, "context_token": state["context_token"],
        "item_list": [{"type": 1, "text_item": {"text": text[:3000]}}],
    }}, state)
    return r.get("ret", 0) == 0 and r.get("errcode", 0) == 0


def notify(title, body):
    print(f"\n[通知] {title}", flush=True)
    try:
        if send_wechat(f"【{title}】\n{body}"):
            return
        if key := secrets().get("SERVERCHAN_KEY"):
            requests.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title[:32], "desp": body}, timeout=10)
    except (requests.RequestException, ValueError) as e:
        print(f"推送失败：{e}", flush=True)
