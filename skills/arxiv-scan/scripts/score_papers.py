#!/usr/bin/env python3
"""
score_papers.py —— 用 DeepSeek 模型对【全部当日论文】逐篇打相关度分(0-10)+ 一句话中文概括。
依据是 config/interest.md 里的「研究方向 prose」。分批发送以省调用。

依赖:requests(系统 python3 自带)。key 从环境变量 DEEPSEEK_API_KEY 读(或 --api-key)。

用法:
  python3 score_papers.py --listing ../.cache/listing_DATE.json --out ../.cache/scored_DATE.json
"""
import argparse, json, os, re, sys, time
try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)
try:
    import yaml
except Exception:
    sys.stderr.write("缺少 pyyaml\n"); sys.exit(2)

API_URL = "https://api.deepseek.com/chat/completions"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config", "config.yaml")
DEFAULT_INTEREST = os.path.join(SKILL_DIR, "config", "interest.md")

SYS_TMPL = """你是文献初筛助手。下面是用户的研究兴趣描述,以及一批 arxiv 论文(标题+摘要)。
对【每一篇】判定它与该研究兴趣的相关档位(四选一),并给一句话中文概括。

研究兴趣描述:
\"\"\"
{interest}
\"\"\"

相关档位(只能填这四个中文标签之一):
- 非常相关   = 正中用户核心方向(用户描述里"非常相关"那类)。
- 相关       = 沾边、或方法/对象部分相关、可借鉴(用户描述里"相关"那类)。
- 不相关     = 在大方向内但不是用户关心的,或用户明确不关心的。
- 完全不相关 = 完全在用户领域之外。
严格按用户描述判定,不要凭论文本身热门程度。

输出要求:**只输出一个 JSON 数组**,每篇一个对象,顺序与输入编号一致:
[{{"i": 编号, "level": "非常相关|相关|不相关|完全不相关 其一", "one_line": "≤40字中文概括(从摘要提炼,说清做了什么)"}}]
不要输出数组以外的任何文字。"""

# 档位 → 排序权重(用于"通过的"内部排序 + 命中过多时按档位截断)
LEVELS = ["非常相关", "相关", "不相关", "完全不相关"]
LEVEL_RANK = {"非常相关": 3, "相关": 2, "不相关": 1, "完全不相关": 0}


def load_cfg(p):
    with open(p, "r", encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    o = c.setdefault("options", {}) or {}
    o.setdefault("score_model", "deepseek-v4-flash")
    o.setdefault("batch_size", 12)
    c["options"] = o
    return c


def extract_json_array(text):
    """从模型回复里抠出 JSON 数组。"""
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


def score_batch(papers, interest, model, key):
    system = SYS_TMPL.format(interest=interest)
    lines = []
    for i, p in enumerate(papers):
        lines.append(f"[{i}] 标题: {p['title']}\n摘要: {p['abstract']}")
    user = "论文列表:\n\n" + "\n\n".join(lines)
    for attempt in range(2):
        try:
            arr = extract_json_array(call(model, key, system, user))
            by_i = {int(o["i"]): o for o in arr if "i" in o}
            res = []
            for i, p in enumerate(papers):
                o = by_i.get(i, {})
                lv = str(o.get("level", "")).strip()
                if lv not in LEVELS:
                    lv = "未判定"
                res.append((lv, str(o.get("one_line", "")).strip()))
            return res
        except Exception as ex:
            sys.stderr.write(f"  [批解析失败 attempt={attempt}] {ex}\n")
            time.sleep(2)
    return [("未判定", "") for _ in papers]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--interest", default=DEFAULT_INTEREST)
    ap.add_argument("--model", default="")
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()

    if not args.api_key:
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY。export 或 --api-key。\n"); sys.exit(3)
    raw_interest = open(args.interest, encoding="utf-8").read()
    # 去掉 HTML 注释(EDIT ME 提示块)后,看是否还有实质内容;顺便不把注释喂给模型
    interest = re.sub(r"<!--.*?-->", "", raw_interest, flags=re.S).strip()
    if len(interest) < 50:
        sys.stderr.write(f"[阻断] interest.md 似乎还没填实质内容,请先写你的研究方向。\n"); sys.exit(3)

    cfg = load_cfg(args.config); opts = cfg["options"]
    model = args.model or opts["score_model"]
    bs = int(opts["batch_size"])
    data = json.load(open(args.listing, encoding="utf-8"))
    papers = data["papers"]
    sys.stderr.write(f"[打分] {len(papers)} 篇 / 模型 {model} / 每批 {bs}\n")

    for start in range(0, len(papers), bs):
        batch = papers[start:start + bs]
        scores = score_batch(batch, interest, model, args.api_key)
        for p, (lv, ol) in zip(batch, scores):
            p["level"] = lv; p["rank"] = LEVEL_RANK.get(lv, -1); p["one_line"] = ol
        sys.stderr.write(f"  批 {start//bs + 1}: {start+len(batch)}/{len(papers)} 完成\n")
        time.sleep(0.5)

    papers.sort(key=lambda p: p.get("rank", -1), reverse=True)
    data["scored"] = True
    out = args.out or args.listing.replace("listing_", "scored_")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    from collections import Counter
    dist = dict(Counter(p.get("level", "未判定") for p in papers))
    top = [f"{p['level']}|{p['title'][:50]}" for p in papers[:8]]
    sys.stderr.write(f"[完成] 档位分布 {dist}\n排名前 8:\n  " + "\n  ".join(top) + f"\n→ {out}\n")
    print(out)


if __name__ == "__main__":
    main()
