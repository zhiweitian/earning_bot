"""定期复盘：用更强的模型审查 agent 最近几轮的工作，结论写进 agent/review.md（下一轮会看到）并推送给人类。
由 supervisor 调用，也可以手动运行：python review.py [覆盖最近几轮，默认 5]
agent 不能修改本文件。"""
import sys
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from supervisor import ROOT, git, notify, secrets, sessions

PROMPT = """你是一个自主运行的挣钱 agent 的外部复盘顾问。这个 agent 由便宜的模型驱动，在循环里一轮轮工作，轮与轮之间只靠记忆文件延续；它可以修改自己的提示词和代码。你每隔几轮看一次它的材料，帮它纠偏。

要求：严格、具体、以证据为准，不客套，不复述材料。重点检查：
- 是否在原地打转：反复做同类事、只调研不落地、产出无法验证、同样的失败重复出现
- 离"真实用户和收入"是近了还是远了；钱花得值不值
- 自我修改是真的解决了问题，还是为改而改、越改越乱
- 记忆是否失真、膨胀，或者和事实不符
- 有没有违反规则或越权的迹象（未经批准的对外操作、试图绕过限制）

输出格式（1500 字以内）：
第一行：结论：正常推进 / 有偏差 / 严重问题，再加一句话概括
## 进展
## 问题（最多 3 个，按严重程度排序，每个都要写出证据）
## 接下来几轮的建议（最多 3 条，具体可执行）
## 给人类的建议（需要人类做的决定或提供的资源；没有就写"无"）"""


def read(path, limit=6000):
    p = ROOT / path
    return p.read_text(encoding="utf-8")[-limit:] if p.exists() else "（无）"


def main(n):
    s = secrets()
    rows = sessions()[-n:]
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "workspace").rglob("*") if p.is_file())
    material = "\n\n".join([
        f"# 人类给的目标\n{read('agent/goal.md')}",
        f"# agent 的记忆\n{read('agent/memory.md')}",
        "# 最近几轮的总结\n" + "\n".join(f"- [{r['time']}] {r['summary']}" for r in rows),
        f"# 上一次复盘（检查建议有没有被执行）\n{read('agent/review.md', 3000)}",
        f"# agent 对自己的修改（git 记录）\n{git('log', f'-{n * 2}', '--stat', '--format=%h %s', '--', 'agent/')[:4000]}",
        f"# workspace 里的文件\n" + "\n".join(files[:150]),
        f"# 账本\n{read('workspace/ledger.md', 3000)}",
        f"# 待人类答复的请求\n{read('workspace/requests.md', 3000)}",
        f"# token 用量（主模型每天）\n{read('logs/usage.json')}",
    ])

    model = s.get("REVIEW_MODEL") or s["EXPERT_MODELS"].split(",")[0]
    client = OpenAI(api_key=s["EXPERT_API_KEY"], base_url=s["EXPERT_BASE_URL"])
    r = client.chat.completions.create(model=model, max_tokens=6000, messages=[
        {"role": "system", "content": PROMPT}, {"role": "user", "content": material}])
    text = r.choices[0].message.content.strip()

    now = datetime.now()
    review = f"（{now:%m-%d %H:%M} 由 {model} 复盘最近 {len(rows)} 轮）\n\n{text}"
    (ROOT / "agent" / "review.md").write_text(review, encoding="utf-8")
    (ROOT / "logs" / "reviews").mkdir(parents=True, exist_ok=True)
    (ROOT / "logs" / "reviews" / f"{now:%Y%m%d-%H%M}.md").write_text(review, encoding="utf-8")
    notify(f"复盘 {text.splitlines()[0].removeprefix('结论：')}", review)
    print(review)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
