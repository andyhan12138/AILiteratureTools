#!/usr/bin/env python3
"""
build_graph.py —— 从种子论文出发,确定性地构造引文候选池(无 LLM)。

两类采集:
  1) 后向闭包:种子的参考文献,再到参考文献的参考文献……深度 D(默认 2)。自然有界。
  2) 前向邻域(固定 2-hop):施引种子的论文 + 这些施引者各自的参考文献(「引它的文章引的文章」)。

爆炸控制:
  - 单节点展开 fan-out 上限 K:任一方向超过 K 篇时,按 citation_count 降序保留前 K。
  - 前向 expand_citers:只对前 N 个高被引施引者再取其参考文献(控调用量)。
  - 去重后候选池总上限 pool_cap N:超出按 citation_count 降序截断。

去重:跨 INSPIRE/ADS 用 canonical_key(DOI>arXiv>recid>bibcode>归一标题)合并,
      合并时取较大的 citation_count、较浅的 depth、并记录 via / 命中的源 / 跨源补齐 id。

输出 JSON:{seeds, params, stats, works:[…]},每条 work 不含正文、含 title+ids+citation+via+depth。
"""
import argparse, json, os, sys
import sources as S

HERE = os.path.dirname(os.path.abspath(__file__))


def merge(pool, rec, depth, via):
    """把一条记录并入池(按 canonical_key 去重合并)。"""
    k = rec.get("key") or S.canonical_key(rec)
    if not k:
        return
    rec["key"] = k
    if k not in pool:
        rec = dict(rec)
        rec["depth"] = depth
        rec["via"] = [via]
        pool[k] = rec
        return
    cur = pool[k]
    # 补齐缺失 id / 取更全的元数据
    for f in ("doi", "arxiv", "recid", "bibcode", "abstract"):
        if not cur.get(f) and rec.get(f):
            cur[f] = rec[f]
    if not cur.get("title") and rec.get("title"):
        cur["title"] = rec["title"]
    if rec.get("citation_count", 0) > cur.get("citation_count", 0):
        cur["citation_count"] = rec["citation_count"]
    if rec.get("year") and not cur.get("year"):
        cur["year"] = rec["year"]
    cur["depth"] = min(cur.get("depth", 99), depth)
    if via not in cur["via"]:
        cur["via"].append(via)
    for s in rec.get("sources", []):
        if s not in cur.setdefault("sources", []):
            cur["sources"].append(s)


def topcap(recs, cap):
    """按被引降序保留前 cap;reference 常无被引数则退化为原序前 cap。"""
    recs = [r for r in recs if r and (r.get("title") or r.get("recid") or r.get("bibcode"))]
    recs.sort(key=lambda r: r.get("citation_count", 0) or 0, reverse=True)
    return recs[:cap]


def expand_backward(clients, node, cap):
    out = []
    for c in clients:
        try:
            out += c.backward(node, cap)
        except Exception as ex:
            sys.stderr.write(f"  [backward {c.name} 出错] {ex}\n")
    return topcap(out, cap)


def expand_forward(clients, node, cap):
    out = []
    for c in clients:
        try:
            out += c.forward(node, cap)
        except Exception as ex:
            sys.stderr.write(f"  [forward {c.name} 出错] {ex}\n")
    return topcap(out, cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", required=True, help="种子标识:arxiv/doi/recid/bibcode/标题")
    ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=2, help="后向闭包深度")
    ap.add_argument("--forward", type=int, default=1, help="1=做前向, 0=只后向")
    ap.add_argument("--fanout-cap", type=int, default=200)
    ap.add_argument("--expand-citers", type=int, default=150)
    ap.add_argument("--pool-cap", type=int, default=3000)
    args = ap.parse_args()

    clients = S.build_clients()
    K, D = args.fanout_cap, args.depth

    # 0) 解析种子(两库都试,合并;记录 seed keys 以便最后排除)
    pool = {}
    seed_keys = set()
    seed_recs = []
    for sid in args.seeds:
        kind, val = S.classify_id(sid)
        got = None
        for c in clients:
            try:
                rec = c.resolve(kind, val)
            except Exception as ex:
                sys.stderr.write(f"  [resolve {c.name} 出错] {ex}\n"); rec = None
            if rec:
                merge(pool, rec, depth=0, via="seed")
                got = rec if got is None else got
                # 把跨源 id 也补到同一 key 上
        if got is None:
            sys.stderr.write(f"⚠️  种子无法解析,跳过: {sid}\n"); continue
        k = S.canonical_key(got)
        seed_keys.add(k)
        seed_recs.append(pool[k])
        sys.stderr.write(f"✓ 种子: {got['title'][:70]}  [recid={got.get('recid')} bibcode={got.get('bibcode')} cites={got.get('citation_count')}]\n")
    if not seed_recs:
        sys.stderr.write("[阻断] 没有任何种子被解析成功。\n"); sys.exit(3)

    # 1) 后向闭包:深度 D
    sys.stderr.write(f"== 后向闭包 (depth={D}, K={K}) ==\n")
    frontier = list(seed_recs)
    for d in range(1, D + 1):
        nxt = []
        for i, node in enumerate(frontier, 1):
            refs = expand_backward(clients, node, K)
            sys.stderr.write(f"  [d{d} {i}/{len(frontier)}] {node['title'][:45]} → 参考文献 {len(refs)}\n")
            for r in refs:
                before = r["key"] in pool if r.get("key") else False
                merge(pool, r, depth=d, via="backward")
                kk = r.get("key") or S.canonical_key(r)
                if kk and not before:
                    nxt.append(pool[kk])
        frontier = nxt
        if not frontier:
            break

    # 2) 前向邻域:施引者(depth1) + 施引者的参考文献(depth2)
    if args.forward:
        sys.stderr.write(f"== 前向邻域 (施引 + 施引者参考文献, K={K}, expand_citers={args.expand_citers}) ==\n")
        all_citers = []
        for i, seed in enumerate(seed_recs, 1):
            citers = expand_forward(clients, seed, K)
            sys.stderr.write(f"  [施引 {i}/{len(seed_recs)}] {seed['title'][:45]} → 施引 {len(citers)}\n")
            for c in citers:
                merge(pool, c, depth=1, via="forward-citers")
                all_citers.append(c)
        if D>1:
            # 只对前 expand_citers 个高被引施引者再取其参考文献
            all_citers = topcap(all_citers, args.expand_citers)
            sys.stderr.write(f"  对 {len(all_citers)} 个高被引施引者取其参考文献…\n")
            for i, citer in enumerate(all_citers, 1):
                refs = expand_backward(clients, citer, K)
                if i % 20 == 0 or i == len(all_citers):
                    sys.stderr.write(f"    [{i}/{len(all_citers)}] 累计池 {len(pool)}\n")
                for r in refs:
                    merge(pool, r, depth=2, via="forward-refs")

    # 3) 去种子 + 总池截断
    #    截断优先级:先按 depth 升序(第一层 > 第二层,保证第一层不被第二层挤掉),
    #    同层内再按 citation_count 降序。这样 pool_cap 截断时先砍掉低被引的【第二层】。
    works = [w for k, w in pool.items() if k not in seed_keys]
    n_before = len(works)
    works.sort(key=lambda w: (w.get("depth", 99), -(w.get("citation_count", 0) or 0)))
    truncated = n_before > args.pool_cap
    works = works[: args.pool_cap]
    from collections import Counter
    depth_dist_before = dict(Counter(w.get("depth") for w in
                              [x for k, x in pool.items() if k not in seed_keys]))
    depth_dist_kept = dict(Counter(w.get("depth") for w in works))

    # 瘦身:候选池阶段不带 abstract(留给 make_cards 阶段按需取),减小体积
    slim = []
    for w in works:
        slim.append({k: w.get(k) for k in ("key", "title", "year", "doi", "arxiv",
                     "recid", "bibcode", "citation_count", "authors", "depth", "via", "sources")})

    stats = {"seeds_resolved": len(seed_recs), "pool_unique": n_before,
             "pool_kept": len(slim), "pool_truncated": truncated,
             "depth_dist_before": depth_dist_before, "depth_dist_kept": depth_dist_kept,
             "sources": [c.name for c in clients]}
    out = {"seeds": [{"key": s["key"], "title": s["title"], "recid": s.get("recid"),
                      "bibcode": s.get("bibcode"), "arxiv": s.get("arxiv"), "doi": s.get("doi"),
                      "year": s.get("year"), "citation_count": s.get("citation_count"),
                      "abstract": s.get("abstract", "")} for s in seed_recs],
           "params": vars(args), "stats": stats, "works": slim}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    sys.stderr.write(f"[完成] 去重后 {n_before} 篇,保留 {len(slim)}"
                     + (f"(超 {args.pool_cap} 已按被引截断)" if truncated else "") + f" → {args.out}\n")
    print(args.out)


if __name__ == "__main__":
    main()
