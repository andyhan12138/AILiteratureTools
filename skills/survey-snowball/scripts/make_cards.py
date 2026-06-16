#!/usr/bin/env python3
"""
make_cards.py —— 对入选的 ≤M 篇,抓 abstract,再用 DeepSeek-V4-flash 出【面向证据的卡片】。

每张卡片(结构化):
  problem   这篇解决什么问题
  method    用了什么方法/数据
  result    关键结果/结论
  stance    对【本次调研的思路】是 支持 / 证伪 / 中立 / 不确定
  reason    ≤40字:为何是这个立场(从摘要里的证据出发)

这是初筛性质(最终人看),所以用便宜的 flash;立场只是粗判线索,不当定论。
摘要按需从 INSPIRE/ADS 取(候选池阶段不带正文)。输入是 snowball.py 选好的入选清单 JSON。
"""
import argparse, json, os, re, sys, time
try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)
import sources as S

API_URL = "https://api.deepseek.com/chat/completions"

SYS_TMPL = """你在帮用户调研某个【思路/主题】的可行性与前人工作。下面是该调研的相关性标尺,以及若干论文(标题+摘要)。
对【每一篇】产出一张面向证据的卡片,严格依据摘要原文,不要脑补摘要里没有的数字/结论。

调研标尺(判断"支持/证伪"时以此为参照):
\"\"\"
{interest}
\"\"\"

每篇输出对象字段:
- problem : 这篇解决什么问题(≤30字)
- method  : 用了什么方法/数据(≤30字)
- result  : 关键结果/结论(≤40字,有数字就写数字)
- stance  : 对上面那个【思路】而言是 "支持" / "证伪" / "中立" / "不确定" 其一
- reason  : ≤40字,为何是这个立场(基于摘要证据;摘要不足以判断就写"不确定")

输出要求:**只输出一个 JSON 数组**,顺序与输入编号一致:
[{{"i":编号,"problem":"…","method":"…","result":"…","stance":"…","reason":"…"}}]
不要输出数组以外任何文字。"""


def extract_json_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    a, b = text.find("["), text.rfind("]")
    if a == -1 or b == -1 or b < a:
        raise ValueError("找不到 JSON 数组")
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


def enrich_abstract(work, clients):
    """按可用 id 取摘要,任一源命中即停。"""
    if work.get("abstract"):
        return work["abstract"]
    tries = []
    if work.get("arxiv"):
        tries.append(("arxiv", work["arxiv"]))
    if work.get("doi"):
        tries.append(("doi", work["doi"]))
    if work.get("bibcode"):
        tries.append(("bibcode", work["bibcode"]))
    if work.get("recid"):
        tries.append(("recid", str(work["recid"])))
    for c in clients:
        for kind, val in tries:
            try:
                rec = c.resolve(kind, val)
            except Exception:
                rec = None
            if rec and rec.get("abstract"):
                return rec["abstract"]
    return ""


def card_batch(items, interest, model, key):
    system = SYS_TMPL.format(interest=interest)
    lines = []
    for i, w in enumerate(items):
        ab = (w.get("abstract") or "")[:1800]
        lines.append(f"[{i}] 标题: {w.get('title','')}\n摘要: {ab if ab else '(无摘要,仅凭标题判断,可填不确定)'}")
    user = "论文:\n\n" + "\n\n".join(lines)
    for attempt in range(2):
        try:
            arr = extract_json_array(call(model, key, system, user))
            by_i = {int(o["i"]): o for o in arr if "i" in o}
            out = []
            for i in range(len(items)):
                o = by_i.get(i, {})
                out.append({k: str(o.get(k, "")).strip() for k in
                            ("problem", "method", "result", "stance", "reason")})
            return out
        except Exception as ex:
            sys.stderr.write(f"  [卡片批解析失败 attempt={attempt}] {ex}\n")
            time.sleep(2)
    return [{"problem": "", "method": "", "result": "", "stance": "不确定", "reason": "解析失败"} for _ in items]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selected", required=True, help="入选清单 JSON(snowball.py 产出)")
    ap.add_argument("--interest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--batch-size", type=int, default=6)
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY。\n"); sys.exit(3)

    interest = open(args.interest, encoding="utf-8").read().strip()
    sel = json.load(open(args.selected, encoding="utf-8"))
    works = sel["selected"]
    clients = S.build_clients()

    sys.stderr.write(f"[抓摘要] {len(works)} 篇…\n")
    for i, w in enumerate(works, 1):
        w["abstract"] = enrich_abstract(w, clients)
        if i % 10 == 0 or i == len(works):
            sys.stderr.write(f"  {i}/{len(works)}\n")

    sys.stderr.write(f"[出卡片] {args.model} / 每批 {args.batch_size}\n")
    bs = args.batch_size
    for start in range(0, len(works), bs):
        batch = works[start:start + bs]
        cards = card_batch(batch, interest, args.model, args.api_key)
        for w, c in zip(batch, cards):
            w["card"] = c
        sys.stderr.write(f"  批 {start//bs + 1}: {start+len(batch)}/{len(works)}\n")
        time.sleep(0.4)

    sel["selected"] = works
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sel, f, ensure_ascii=False, indent=2)
    from collections import Counter
    st = dict(Counter((w.get("card") or {}).get("stance", "?") for w in works))
    sys.stderr.write(f"[完成] 立场分布 {st} → {args.out}\n")
    print(args.out)


if __name__ == "__main__":
    main()
