#!/usr/bin/env python3
"""
snowball.py —— 编排器:种子论文 → 引文图 → title 初筛 → abstract 卡片 → 三轴综述。

一条命令跑完:
  1) build_graph.py   INSPIRE+ADS 拉后向闭包 + 前向邻域 → 去重截断 → 候选池(第一层>第二层)
  2) 拼相关性标尺      按 --topic / --use-interest / --use-seed 组合(至少一项)
  3) score_titles.py  DeepSeek-flash 按 title 判四档
  4) 选入选 ≤M 篇      pass_levels 内,排序【相关性 → 第一层优先 → 被引】,封顶 M
  5) make_cards.py     抓 abstract + flash 出逐篇证据卡片
  6) synthesize.py     DeepSeek-V4-pro 出三轴综述
  7) 落盘              notes/snowball/<name>/  report.md + cards.md + candidates.md + *.json

需要 DEEPSEEK_API_KEY;ADS 需 ADS_DEV_KEY(缺则只用 INSPIRE)。建议用 zsh -ic 包一层。
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
CACHE = os.path.join(SKILL_DIR, ".cache")
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config", "config.yaml")
DEFAULT_INTEREST = os.path.join(SKILL_DIR, "config", "interest.md")
PY = sys.executable or "python3"
LEVEL_RANK = {"非常相关": 3, "相关": 2, "不相关": 1, "完全不相关": 0, "未判定": -1}


def run(cmd):
    sys.stderr.write("  $ " + " ".join(os.path.basename(c) if str(c).endswith('.py') else str(c) for c in cmd) + "\n")
    sys.stderr.flush()
    # 启动子进程，不捕获输出，直接继承父进程的标准流
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    output_lines = []
    # 逐行读取并实时输出（同时保存用于返回值）
    for line in p.stdout:
        sys.stderr.write(line)
        sys.stderr.flush()
        output_lines.append(line.strip())
    return_code = p.wait()
    if return_code != 0:
        raise RuntimeError(f"子步骤失败(exit {return_code})")
    return (output_lines[-1] if output_lines else "")

def load_cfg(p):
    import yaml
    c = yaml.safe_load(open(p, encoding="utf-8")) or {}
    g = c.setdefault("graph", {}); g.setdefault("backward_depth", 2); g.setdefault("forward", True)
    g.setdefault("fanout_cap", 200); g.setdefault("expand_citers", 150); g.setdefault("pool_cap", 3000)
    s = c.setdefault("screen", {}); s.setdefault("score_model", "deepseek-v4-flash")
    s.setdefault("batch_size", 15); s.setdefault("pass_levels", ["非常相关", "相关"]); s.setdefault("analyze_cap", 40)
    y = c.setdefault("synthesize", {}); y.setdefault("card_model", "deepseek-v4-flash"); y.setdefault("report_model", "deepseek-v4-pro")
    c.setdefault("out_dir", "notes/snowball")
    return c


def slugify(s, n=40):
    s = re.sub(r"[^\w一-鿿]+", "-", (s or "").strip()).strip("-")
    return (s[:n] or "snowball")


def build_interest(topic, use_interest, use_seed, interest_path, seeds):
    parts = []
    if topic:
        parts.append("== 本次调研的思路/主题(最高优先) ==\n" + topic.strip())
    if use_interest and os.path.exists(interest_path):
        raw = open(interest_path, encoding="utf-8").read()
        prof = re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()
        if len(prof) >= 30:
            parts.append("== 长期研究画像 ==\n" + prof)
    if use_seed and seeds:
        sl = ["== 种子论文(主题锚) =="]
        for s in seeds:
            ab = (s.get("abstract") or "")[:400]
            sl.append(f"- {s.get('title','')} ({s.get('year','?')}): {ab}")
        parts.append("\n".join(sl))
    return "\n\n".join(parts).strip()


def select(works, pass_levels, cap):
    """入选排序:相关性档(高→低) → 第一层优先(depth 小→大) → 被引(高→低)。封顶 cap。"""
    cand = [w for w in works if w.get("level") in pass_levels]
    cand.sort(key=lambda w: (-LEVEL_RANK.get(w.get("level"), -1),
                             w.get("depth", 99),
                             -(w.get("citation_count", 0) or 0)))
    return cand[:cap], len(cand) > cap


def link_of(w):
    if w.get("arxiv"):
        return f"https://arxiv.org/abs/{w['arxiv']}"
    if w.get("doi"):
        return f"https://doi.org/{w['doi']}"
    if w.get("bibcode"):
        return f"https://ui.adsabs.harvard.edu/abs/{w['bibcode']}"
    if w.get("recid"):
        return f"https://inspirehep.net/literature/{w['recid']}"
    return ""


def marker(w):
    if w.get("arxiv"):
        return f"arXiv:{w['arxiv']}"
    if w.get("doi"):
        return f"DOI:{w['doi']}"
    au = (w.get("authors") or ["?"])[0].split(",")[0]
    return f"{au} {w.get('year','')}".strip()


def render_cards(works):
    L = [f"# 逐篇证据卡片({len(works)} 篇)\n",
         "> DeepSeek-flash 初筛性质,立场仅为线索,最终以人工阅读为准。\n"]
    for i, w in enumerate(works, 1):
        c = w.get("card") or {}
        au = ", ".join((w.get("authors") or [])[:3]) + (" et al." if len(w.get("authors") or []) > 3 else "")
        L.append(f"\n## {i}. [{w.get('level','?')} · 层{w.get('depth','?')} · 被引{w.get('citation_count',0)}] {w.get('title','')}\n")
        L.append(f"- {marker(w)} · [{link_of(w)}]({link_of(w)})\n")
        L.append(f"- 作者: {au} · 年份: {w.get('year','?')} · 来源: {'/'.join(w.get('sources',[]))} · 路径: {'/'.join(w.get('via',[]))}\n")
        L.append(f"- **问题**: {c.get('problem','')}\n- **方法**: {c.get('method','')}\n- **结果**: {c.get('result','')}\n")
        L.append(f"- **对本思路立场**: **{c.get('stance','?')}** — {c.get('reason','')}\n")
        ab = (w.get("abstract") or "").strip()
        if ab:
            # 摘要默认折叠,点 <summary> 展开(GitHub 等支持 <details>)
            L.append(f"\n<details>\n<summary>📄 摘要(点击展开)</summary>\n\n{ab}\n\n</details>\n")
    return "".join(L)


def render_candidates(works):
    def table(rows):
        out = ["| 档 | 层 | 被引 | 年 | 立场 | 标题 | 标记 |\n|:--:|:--:|:--:|:--:|:--:|------|------|\n"]
        for w in rows:
            c = w.get("card") or {}
            t = (w.get("title") or "").replace("|", "\\|")
            out.append(f"| {w.get('level','?')} | {w.get('depth','?')} | {w.get('citation_count',0)} | "
                       f"{w.get('year','?')} | {c.get('stance','?')} | [{t}]({link_of(w)}) | {marker(w)} |\n")
        return "".join(out)
    by_rel = sorted(works, key=lambda w: (-LEVEL_RANK.get(w.get("level"), -1), w.get("depth", 99), -(w.get("citation_count", 0) or 0)))
    by_cit = sorted(works, key=lambda w: -(w.get("citation_count", 0) or 0))
    return (f"# 入选候选({len(works)} 篇)\n\n## 按相关性排序(相关性→第一层优先→被引)\n\n"
            + table(by_rel) + "\n## 按被引数排序\n\n" + table(by_cit))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", required=True)
    ap.add_argument("--topic", default="", help="本次调研的思路/主题(相关性标尺 a)")
    ap.add_argument("--use-interest", action="store_true", help="把 interest.md 标准画像并入标尺(b)")
    ap.add_argument("--no-seed", action="store_true", help="不把种子主题并入标尺(默认会用,c)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--interest-file", default=DEFAULT_INTEREST)
    ap.add_argument("--name", default="", help="输出子目录名;默认据 topic/种子生成")
    ap.add_argument("--depth", type=int, default=0)
    ap.add_argument("--no-forward", action="store_true")
    ap.add_argument("--analyze-cap", type=int, default=0)
    ap.add_argument("--reuse", action="store_true", help="复用 .cache 已完成步骤")
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    cfg = load_cfg(args.config)
    g, scr, syn = cfg["graph"], cfg["screen"], cfg["synthesize"]
    depth = args.depth or g["backward_depth"]
    cap = args.analyze_cap or scr["analyze_cap"]
    tag = slugify(args.name or args.topic or args.seeds[0])

    if not os.environ.get("DEEPSEEK_API_KEY"):
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY(打分/卡片/综述都要)。\n"); sys.exit(3)

    # 1) build graph
    sys.stderr.write("== 1/5 构造引文图 ==\n")
    graph = os.path.join(CACHE, f"graph_{tag}.json")
    if not (args.reuse and os.path.exists(graph)):
        run([PY, os.path.join(HERE, "build_graph.py"), "--seeds", *args.seeds, "--out", graph,
             "--depth", str(depth), "--forward", "0" if args.no_forward else "1",
             "--fanout-cap", str(g["fanout_cap"]), "--expand-citers", str(g["expand_citers"]),
             "--pool-cap", str(g["pool_cap"])])
    data = json.load(open(graph, encoding="utf-8"))
    data = json.load(open(graph, encoding="utf-8"))
    seeds = data["seeds"]
    sys.stderr.write(f"候选池 {data['stats']['pool_kept']} 篇 · 层分布(保留){data['stats']['depth_dist_kept']}\n")

    # 2) 拼相关性标尺
    interest_txt = build_interest(args.topic, args.use_interest, not args.no_seed, args.interest_file, seeds)
    if len(interest_txt) < 20:
        sys.stderr.write("[阻断] 相关性标尺为空(topic/interest/seed 都没给到内容)。\n"); sys.exit(3)
    interest_path = os.path.join(CACHE, f"interest_{tag}.txt")
    open(interest_path, "w", encoding="utf-8").write(interest_txt)

    # 3) title 打分
    sys.stderr.write("== 2/5 title 初筛 ==\n")
    if not (args.reuse and data.get("title_scored")):
        run([PY, os.path.join(HERE, "score_titles.py"), "--graph", graph, "--interest", interest_path,
             "--model", scr["score_model"], "--batch-size", str(scr["batch_size"])])
        data = json.load(open(graph, encoding="utf-8"))

    # 4) 选入选
    selected, truncated = select(data["works"], list(scr["pass_levels"]), cap)
    from collections import Counter
    sys.stderr.write(f"== 3/5 入选 {len(selected)} 篇(pass={scr['pass_levels']}, M={cap}"
                     + ("，已截断" if truncated else "") + f");层分布 {dict(Counter(w.get('depth') for w in selected))} ==\n")
    sel_path = os.path.join(CACHE, f"selected_{tag}.json")
    json.dump({"seeds": seeds, "selected": selected, "truncated": truncated},
              open(sel_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 5) 卡片
    sys.stderr.write("== 4/5 逐篇证据卡片 ==\n")
    cards_path = os.path.join(CACHE, f"cards_{tag}.json")
    if not (args.reuse and os.path.exists(cards_path)):
        run([PY, os.path.join(HERE, "make_cards.py"), "--selected", sel_path, "--interest", interest_path,
             "--out", cards_path, "--model", syn["card_model"]])
    carded = json.load(open(cards_path, encoding="utf-8"))

    # 6) 综述
    sys.stderr.write("== 5/5 三轴综述 ==\n")
    report_cache = os.path.join(CACHE, f"report_{tag}.md")
    if not (args.reuse and os.path.exists(report_cache)):
        run([PY, os.path.join(HERE, "synthesize.py"), "--cards", cards_path, "--interest", interest_path,
             "--out", report_cache, "--model", syn["report_model"]])

    # 7) 落盘
    out_dir = os.path.join(os.getcwd(), cfg["out_dir"], tag)
    os.makedirs(out_dir, exist_ok=True)
    works = carded["selected"]
    fm = (f"---\nsnowball: survey-snowball\nseeds: {[s.get('title','')[:40] for s in seeds]}\n"
          f"sources: {data['stats']['sources']}\npool_kept: {data['stats']['pool_kept']}\n"
          f"selected: {len(works)}\npass_levels: {scr['pass_levels']}\n"
          f"report_model: {syn['report_model']}\n---\n\n")
    open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8").write(fm + open(report_cache, encoding="utf-8").read())
    open(os.path.join(out_dir, "cards.md"), "w", encoding="utf-8").write(render_cards(works))
    open(os.path.join(out_dir, "candidates.md"), "w", encoding="utf-8").write(render_candidates(works))
    json.dump(carded, open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    sys.stderr.write(f"\n[完成] 种子 {len(seeds)} · 候选池 {data['stats']['pool_kept']} · 入选 {len(works)}\n"
                     f"→ {out_dir}/  (report.md / cards.md / candidates.md / data.json)\n")
    print(out_dir)


if __name__ == "__main__":
    main()
