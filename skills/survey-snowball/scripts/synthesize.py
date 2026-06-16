#!/usr/bin/env python3
"""
synthesize.py —— 用 DeepSeek-V4-pro 把入选论文的卡片+摘要综合成【三轴综述报告】。

三轴(用户要的):
  1. 前人做了什么  —— 按子思路/流派分组,带时间演化脉络
  2. 可行性        —— 已被验证的 / 相互矛盾的 / 卡点在哪(汇总卡片里的"支持/证伪"立场)
  3. 突破口        —— 没人做过的缺口、未来能推的方向
  4. must-read     —— 关键论文短名单

正文引用沿用工具链约定:用【可手动检索到该文献的标记】(arXiv:xxxx / DOI / 作者 年份),不杜撰编号。
"""
import argparse, json, os, re, sys
try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)

API_URL = "https://api.deepseek.com/chat/completions"

SYS = """你是资深科研综述写作者。用户在调研某个【思路/主题】的可行性与前人工作,给了你一批已初筛+逐篇卡片化的论文。
请基于这些材料写一份结构化中文综述报告(Markdown)。严格依据所给材料,**不要编造材料里没有的结论或数字**;材料不足之处明说"现有材料未覆盖"。

报告结构(就用这四个一级/二级标题,内部可再分子标题):

## 1. 前人做了什么
按【子思路 / 技术路线 / 流派】分组归纳,每组讲清代表工作、核心做法、随时间的演化脉络(早期→近期)。这是 landscape。

## 2. 可行性
聚合各篇对该思路的证据立场:哪些结果**支持**其可行、哪些**证伪**或暴露困难、哪里彼此**矛盾**、当前主要**卡点/前提条件**是什么。要给出有依据的判断,不要和稀泥。

## 3. 突破口
指出没人做过的缺口、悬而未决的问题、方法或数据上的瓶颈,以及未来最值得推进的方向。

## 4. must-read 关键论文
8~15 篇最该先读的,每篇一行:`标记 — 一句话为什么重要`。

引用规范:正文提到某篇时,在括号里给【可检索标记】——优先 `arXiv:xxxx`,否则 `DOI:...`,再否则 `作者 年份`。这些标记我下面会随每篇一起给你。不要编造不存在的编号。
"""


def call(model, key, system, user, timeout=600):
    payload = {"model": model, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user}], "temperature": 0.3, "stream": False}
    r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}",
                      "Content-Type": "application/json"}, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def marker(w):
    if w.get("arxiv"):
        return f"arXiv:{w['arxiv']}"
    if w.get("doi"):
        return f"DOI:{w['doi']}"
    au = (w.get("authors") or ["?"])[0].split(",")[0]
    return f"{au} {w.get('year','')}".strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", required=True, help="make_cards 产出的 JSON")
    ap.add_argument("--interest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY。\n"); sys.exit(3)

    interest = open(args.interest, encoding="utf-8").read().strip()
    data = json.load(open(args.cards, encoding="utf-8"))
    works = data["selected"]
    seeds = data.get("seeds", [])

    blocks = []
    for w in works:
        c = w.get("card") or {}
        ab = (w.get("abstract") or "")[:700]
        blocks.append(
            f"[{marker(w)}] ({w.get('year','?')}, 被引{w.get('citation_count',0)}, "
            f"层{w.get('depth','?')}, {w.get('level','?')})\n"
            f"标题: {w.get('title','')}\n"
            f"问题: {c.get('problem','')} | 方法: {c.get('method','')} | 结果: {c.get('result','')}\n"
            f"对本思路立场: {c.get('stance','?')} ({c.get('reason','')})\n"
            f"摘要: {ab}")
    seed_line = "; ".join(f"{s.get('title','')} [{ ('arXiv:'+s['arxiv']) if s.get('arxiv') else (s.get('recid') or s.get('bibcode') or '') }]" for s in seeds)

    user = (f"【本次调研的相关性标尺/思路】\n{interest}\n\n"
            f"【种子论文】\n{seed_line}\n\n"
            f"【入选论文({len(works)} 篇,已逐篇卡片化)】\n\n" + "\n\n".join(blocks)
            + "\n\n请据此产出四段式综述报告(Markdown)。")

    sys.stderr.write(f"[综述] {len(works)} 篇 → {args.model} …\n")
    report = call(args.model, args.api_key, SYS, user)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report.strip() + "\n")
    sys.stderr.write(f"[完成] 综述 → {args.out}\n")
    print(args.out)


if __name__ == "__main__":
    main()
