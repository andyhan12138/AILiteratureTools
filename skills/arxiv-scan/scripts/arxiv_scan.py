#!/usr/bin/env python3
"""
arxiv_scan.py —— 编排器:一条命令跑完「刷 arxiv → 模型初筛 → 全文总结 → 每日整合」。

流程:
  1) fetch_arxiv.py   拉当日该板块全部论文(无关键词过滤)
  2) score_papers.py  逐篇打分 0-10 + 一句话(模型,依据 interest.md)
  3) 选 score>=阈值 的(至多 max_summarize 篇)
  4) 对选中的:fetch_fulltext.py 取全文 → summarize_paper.py 全文总结
  5) 整合日报 md:通过初筛(全文总结) + 初筛掉(题目+一句话)

需要环境变量 DEEPSEEK_API_KEY。建议用:
  zsh -ic 'python3 skills/arxiv-scan/scripts/arxiv_scan.py --date 2026-06-11'

用法:
  python3 arxiv_scan.py --date today
  python3 arxiv_scan.py --date 2026-06-11 --limit 25   # 测试:只抓前25篇
"""
import argparse, json, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
CACHE = os.path.join(SKILL_DIR, ".cache")
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config", "config.yaml")
PY = sys.executable or "python3"


def run(cmd):
    """跑子脚本,stderr 透传,返回最后一行 stdout(脚本约定打印产物路径)。"""
    sys.stderr.write("  $ " + " ".join(os.path.basename(c) if c.endswith('.py') else c for c in cmd) + "\n")
    p = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(p.stderr)
    if p.returncode != 0:
        raise RuntimeError(f"子步骤失败(exit {p.returncode}): {cmd}")
    return (p.stdout.strip().splitlines() or [""])[-1]


def load_cfg(p):
    import yaml
    c = yaml.safe_load(open(p, encoding="utf-8")) or {}
    o = c.setdefault("options", {}) or {}
    o.setdefault("pass_levels", ["非常相关", "相关"])
    o.setdefault("max_summarize", 15)
    o.setdefault("summary_model", "deepseek-v4-flash")
    o.setdefault("out_dir", "notes/arxiv_daily")
    c["options"] = o
    return c


def assemble(data, passed, filtered, summaries, opts, truncated, pass_levels, cap):
    d = data["date"]
    cats = ", ".join(data["categories"])
    fm = (f"---\ndate: {d}\ncategories: [{cats}]\nscan: arxiv-scan(模型初筛·四档)\n"
          f"total_fetched: {data['total']}\nscored: {len([p for p in data['papers'] if p.get('level')])}\n"
          f"pass_levels: [{', '.join(pass_levels)}]\npassed: {len(passed)}\n"
          f"score_model: {opts.get('score_model','?')}\nsummary_model: {opts['summary_model']}\n---\n\n")
    L = [fm, f"# arxiv 初筛日报 · {d}\n",
         f"> 板块 `{cats}` · 抓取 **{data['total']}** 篇 · 模型四档初筛 · "
         f"通过({'/'.join(pass_levels)}) **{len(passed)}** 篇\n"]
    if truncated:
        L.append(f"> ⚠️ 通过的多于上限 {cap},已按档位从高到低截断;其余仍列在下方表格。\n")

    L.append(f"\n## 通过初筛 · 全文总结({len(passed)} 篇)\n")
    if not passed:
        L.append("\n_今日无论文进入前两档。_\n")
    for p in passed:
        au = p["authors"]; au = ", ".join(au[:3]) + (" et al." if len(au) > 3 else "")
        L.append(f"\n### [{p.get('level','?')}] {p['title']}\n")
        L.append(f"- arxiv: {p['abs_url']} · HTML: {p['html_url']}\n")
        L.append(f"- 作者: {au} · 主类目: {p['primary_category']}\n")
        s = summaries.get(p["id"])
        L.append("\n" + (s if s else f"_(全文未获取,降级为摘要一句话)_ {p.get('one_line','')}") + "\n")

    L.append(f"\n## 初筛掉 · 题目 + 一句话({len(filtered)} 篇,按档位)\n\n")
    L.append("| 档 | 标题 | 一句话(据摘要) |\n|:--:|------|------|\n")
    for p in filtered:
        t = p["title"].replace("|", "\\|")
        ol = (p.get("one_line", "") or "").replace("|", "\\|")
        L.append(f"| {p.get('level','?')} | [{t}]({p['abs_url']}) | {ol} |\n")
    return "".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--limit", type=int, default=0, help="测试:只抓前N篇")
    ap.add_argument("--max-summarize", type=int, default=0, help=">0 时覆盖 config 的封顶(测试用)")
    ap.add_argument("--reuse", action="store_true", help="复用已存在的中间缓存,跳过已完成步骤")
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    cfg = load_cfg(args.config); opts = cfg["options"]

    # 1) fetch
    sys.stderr.write("== 1/4 抓取当日全部论文 ==\n")
    listing = run([PY, os.path.join(HERE, "fetch_arxiv.py"), "--date", args.date,
                   "--config", args.config] + (["--limit", str(args.limit)] if args.limit else []))
    data = json.load(open(listing, encoding="utf-8"))
    date = data["date"]
    sys.stderr.write(f"\n扫描日期: {date}\n")
    if data.get("total", 0) == 0:
        avail = data.get("available_dates", [])
        hint = f" recent 可扫的公告日: {avail}" if avail else ""
        print(f"⚠️  {date} 不在 arxiv recent 覆盖内 / 当日无公告(周末·节假日·尚未公告)。{hint}")
        return

    # 2) score
    sys.stderr.write("== 2/4 模型打分 ==\n")
    scored = os.path.join(CACHE, f"scored_{date}.json")
    if not (args.reuse and os.path.exists(scored)):
        run([PY, os.path.join(HERE, "score_papers.py"), "--listing", listing,
             "--out", scored, "--config", args.config])
    data = json.load(open(scored, encoding="utf-8"))
    papers = data["papers"]
    # 顺手把 score_model 记进 opts 供 frontmatter
    opts.setdefault("score_model", cfg["options"].get("score_model"))

    # 3) select(档位在 pass_levels 内即通过;papers 已按档位从高到低排序)
    pass_levels = list(opts["pass_levels"]); cap = args.max_summarize or int(opts["max_summarize"])
    above = [p for p in papers if p.get("level") in pass_levels]
    passed = above[:cap]
    truncated = len(above) > cap
    passed_ids = {p["id"] for p in passed}
    filtered = [p for p in papers if p["id"] not in passed_ids]
    sys.stderr.write(f"== 3/4 选中 {len(passed)} 篇({'/'.join(pass_levels)},上限{cap});其余 {len(filtered)} 篇进表格 ==\n")

    # 4) fulltext + summarize
    sys.stderr.write("== 4/4 全文总结 ==\n")
    summaries = {}
    for i, p in enumerate(passed, 1):
        sys.stderr.write(f"  [{i}/{len(passed)}] {p['id_full']} {p['title'][:50]}\n")
        ft = os.path.join(CACHE, f"ft_{p['id']}.txt")
        try:
            run([PY, os.path.join(HERE, "fetch_fulltext.py"), p["id_full"], "--out", ft])
        except Exception as ex:
            sys.stderr.write(f"    全文获取失败,降级摘要: {ex}\n"); continue
        sm = os.path.join(CACHE, f"sum_{p['id']}.md")
        try:
            run([PY, os.path.join(HERE, "summarize_paper.py"), "--input", ft, "--out", sm,
                 "--title", p["title"], "--model", opts["summary_model"]])
            summaries[p["id"]] = open(sm, encoding="utf-8").read().strip()
        except Exception as ex:
            sys.stderr.write(f"    总结失败,降级摘要: {ex}\n")

    # assemble + write
    md = assemble(data, passed, filtered, summaries, opts, truncated, pass_levels, cap)
    ym = date.replace("-", "")[:6]; dd = date.split("-")[2]
    out_dir = os.path.join(os.getcwd(), opts["out_dir"], ym)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{dd}_scan.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    sys.stderr.write(f"\n[完成] {date} · 通过 {len(passed)} 篇 · 日报 → {out}\n")
    print(out)


if __name__ == "__main__":
    main()
