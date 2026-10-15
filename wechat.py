"""微信 ClawBot 桥：把 agent 的请求推送到微信，你在微信里直接答复。
首次使用：python wechat.py login，用微信扫码；然后在微信里给机器人随便发一句话。
之后由 start.sh 在后台运行。在微信里给机器人发：
  #3 批准      答复 3 号请求（编号前的 # 可省略）
  状态         查看最近几轮的总结
  其他任何话   列出所有待答复的请求
没登录 ClawBot 时只推送（走 Server酱），不能在微信里答复。
agent 不能修改本文件。"""
import re
import sys
import time

import requests

from notify import ROOT, ilink, notify, save_wechat_state, send_wechat, wechat_state

BASE = "https://ilinkai.weixin.qq.com"
REQUESTS = ROOT / "workspace" / "requests.md"
SESSIONS = ROOT / "logs" / "sessions.jsonl"
BLOCK = re.compile(r"## #(\d+) \[(\S+)\][^\n]*\n问题：(.*?)\n答复：(.*?)(?=\n## #|\Z)", re.S)


def login():
    q = requests.get(f"{BASE}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=15).json()
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(q["qrcode_img_content"])
        qr.print_ascii(invert=True)
    except ImportError:
        pass
    print(f"用微信扫上面的二维码，或者在浏览器打开这个链接再扫码：\n{q['qrcode_img_content']}")
    while True:
        s = requests.get(f"{BASE}/ilink/bot/get_qrcode_status", params={"qrcode": q["qrcode"]},
                         headers={"iLink-App-ClientVersion": "1"}, timeout=40).json()
        if s.get("status") == "confirmed":
            save_wechat_state({"token": s["bot_token"], "base_url": s.get("baseurl") or BASE})
            print("登录成功。现在在微信里给机器人发一句话（比如：待办），之后它才能主动给你发消息。")
            return
        if s.get("status") == "expired":
            sys.exit("二维码过期了，请重新运行")
        time.sleep(1)


def pending():
    text = REQUESTS.read_text(encoding="utf-8") if REQUESTS.exists() else ""
    return [(n, q.strip()) for n, status, q, a in BLOCK.findall(text) if status == "待答复" and not a.strip()]


def answer(n, reply):
    text = REQUESTS.read_text(encoding="utf-8")

    def fill(m):
        if m.group(1) != n or m.group(2) != "待答复" or m.group(4).strip():
            return m.group(0)
        return m.group(0)[:m.start(4) - m.start()] + reply + "\n"

    REQUESTS.write_text(BLOCK.sub(fill, text), encoding="utf-8")


def handle(text):
    ids = {n for n, _ in pending()}
    m = re.match(r"#?(\d+)\s*[:：]?\s*(.+)", text, re.S)
    if m and m.group(1) in ids:
        answer(m.group(1), m.group(2).strip())
        return f"已记录 #{m.group(1)} 的答复，agent 会在 30 秒内被唤醒。"
    if text == "状态":
        rows = [l for l in SESSIONS.read_text(encoding="utf-8").splitlines() if l][-3:] if SESSIONS.exists() else []
        return "最近几轮：\n" + "\n\n".join(rows) if rows else "还没有运行记录。"
    items = pending()
    if not items:
        return "目前没有待答复的请求。\n\n回复格式：#编号 答复内容；发「状态」查看最近几轮。"
    return "待答复的请求：\n\n" + "\n\n".join(f"#{n} {q}" for n, q in items) + "\n\n回复格式：#编号 答复内容"


def main():
    state = wechat_state()
    notified = set(state.get("notified", []))
    while True:
        for n, q in pending():
            if n not in notified:
                notify(f"#{n} {q[:28]}", f"{q}\n\n直接回复：#{n} 你的答复")
                notified.add(n)
        state = wechat_state()
        state["notified"] = sorted(notified)

        if not state.get("token"):
            save_wechat_state(state)
            time.sleep(30)
            continue
        try:
            r = ilink("ilink/bot/getupdates", {"get_updates_buf": state.get("buf", "")}, state, timeout=45)
        except (requests.RequestException, ValueError):
            time.sleep(10)
            continue
        if r.get("errcode") == -14:
            state.pop("token")
            save_wechat_state(state)
            notify("微信机器人登录过期", "请在电脑上运行 python wechat.py login 重新扫码，期间改用 Server酱推送。")
            continue
        state["buf"] = r.get("get_updates_buf", state.get("buf", ""))
        for msg in r.get("msgs") or []:
            if msg.get("message_type") != 1:
                continue
            text = "".join((i.get("text_item") or {}).get("text", "")
                           for i in msg.get("item_list") or [] if i.get("type") == 1).strip()
            state.update(user=msg["from_user_id"], context_token=msg["context_token"])
            save_wechat_state(state)
            send_wechat(handle(text), state)
        save_wechat_state(state)


if __name__ == "__main__":
    login() if sys.argv[1:] == ["login"] else main()
