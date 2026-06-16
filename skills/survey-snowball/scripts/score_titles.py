#!/usr/bin/env python3
"""
score_titles.py —— 用 DeepSeek-V4-flash 对候选池【按 title 初筛】判四档 + 一句话理由。

判档依据 = 调研相关性标尺(由 snowball.py 按你勾选的 a/b/c 组合拼好,存成一个文本文件传进来):
  (a) 本次调研的临时 topic   (b) interest.md 标准研究画像   (c) 种子论文主题
title 很短、信息少 → 提示模型【拿不准时倾向判"相关"】,把精筛留给后续 abstract 阶段,避免漏掉。

依赖 requests;key 从 DEEPSEEK_API_KEY 读。输入/输出都是 build_graph 的 graph JSON(就地加 level/rank)。
"""
import argparse, json, os, re, sys, time
try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)

API_URL = "https://api.deepseek.com/chat/completions"

SYS_TMPL = """你是文献调研的初筛助手。下面是用户调研某个【思路/主题】时关心的相关性标尺,以及一批候选论文(只给标题,可能还有年份/被引)。
对【每一篇】判定它与该标尺的相关档位(四选一),并给≤30字中文理由。

相关性标尺:
\"\"\"
{interest}
\"\"\"

相关档位(只能填这四个中文标签之一):
- 非常相关   = 标题直指该思路的核心问题/方法/对象。
- 相关       = 标题与该思路沾边、方法或对象部分相关、可能可借鉴。
- 不相关     = 同领域但与该思路无关。
- 完全不相关 = 完全在该思路领域之外。

重要:只给了【标题】,信息有限。**拿不准时倾向判"相关"而非"不相关"**(精筛留给后续摘要阶段,这一步只为砍掉明显无关的)。

输出要求:**只输出一个 JSON 数组**,每篇一个对象,顺序与输入编号一致:
[{{"i": 编号, "level": "非常相关|相关|不相关|完全不相关 其一", "why": "≤30字中文理由"}}]
不要输出数组以外的任何文字。"""

LEVELS = ["非常相关", "相关", "不相关", "完全不相关"]
LEVEL_RANK = {"非常相关": 3, "相关": 2, "不相关": 1, "完全不相关": 0}


def extract_json_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    a, b = text.find("["), text.rfind("]")
    if a == -1 or b == -1 or b < a:
        raise ValueError("回复中找不到 JSON 数组")
    return json.loads(text[a:b + 1])


def call(model, key, system, user, timeout=300):
    payload = {"model": model, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user}], "temperature": 0.2, "stream": False}
    r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}",
                      "Content-Type": "application/json"}, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def score_batch(works, interest, model, key):
    system = SYS_TMPL.format(interest=interest)
    lines = []
    for i, w in enumerate(works):
        meta = []
        if w.get("year"):
            meta.append(str(w["year"]))
        if w.get("citation_count"):
            meta.append(f"被引{w['citation_count']}")
        tag = f" ({', '.join(meta)})" if meta else ""
        lines.append(f"[{i}] {w.get('title', '')}{tag}")
    user = "候选论文(仅标题):\n\n" + "\n".join(lines)
    for attempt in range(2):
        try:
            arr = extract_json_array(call(model, key, system, user))
            by_i = {int(o["i"]): o for o in arr if "i" in o}
            res = []
            for i in range(len(works)):
                o = by_i.get(i, {})
                lv = str(o.get("level", "")).strip()
                if lv not in LEVELS:
                    lv = "未判定"
                res.append((lv, str(o.get("why", "")).strip()))
            return res
        except Exception as ex:
            sys.stderr.write(f"  [批解析失败 attempt={attempt}] {ex}\n")
            time.sleep(2)
    return [("未判定", "") for _ in works]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--interest", required=True, help="拼好的相关性标尺文本文件")
    ap.add_argument("--out", default="")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--batch-size", type=int, default=15)
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY。\n"); sys.exit(3)

    interest = open(args.interest, encoding="utf-8").read().strip()
    if len(interest) < 20:
        sys.stderr.write("[阻断] 相关性标尺为空,无法初筛。\n"); sys.exit(3)

    data = json.load(open(args.graph, encoding="utf-8"))
    works = data["works"]
    bs = args.batch_size
    sys.stderr.write(f"[title 初筛] {len(works)} 篇 / {args.model} / 每批 {bs}\n")
    for start in range(0, len(works), bs):
        batch = works[start:start + bs]
        scores = score_batch(batch, interest, args.model, args.api_key)
        for w, (lv, why) in zip(batch, scores):
            w["level"] = lv; w["rank"] = LEVEL_RANK.get(lv, -1); w["why"] = why
        sys.stderr.write(f"  批 {start//bs + 1}: {start+len(batch)}/{len(works)}\n")
        time.sleep(0.4)

    from collections import Counter
    dist = dict(Counter(w.get("level", "未判定") for w in works))
    data["title_scored"] = True
    out = args.out or args.graph
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sys.stderr.write(f"[完成] 档位分布 {dist} → {out}\n")
    print(out)


if __name__ == "__main__":
    main()
