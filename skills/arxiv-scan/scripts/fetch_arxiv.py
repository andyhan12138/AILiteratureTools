#!/usr/bin/env python3
"""
fetch_arxiv.py —— 按【公告日期】(arxiv 网页 /list/<板块>/recent 那种分日列表)抓取论文。
不是按提交日期(submittedDate)!这样跟你在 arxiv 网页上看到的「Mon, 15 Jun 2026」一致。

做法:
  1) 抓每个板块的 /list/<板块>/recent?show=2000 页,按 <h3>日期(showing…)</h3> 切分节,
     取出【指定日期】那一节里的所有 arXiv id(4 板块合并去重)。
  2) 用 arxiv API 的 id_list= 批量补 标题/摘要/作者/类目。
覆盖范围 = recent 页现含的最近若干公告日(通常 5 天);请求日期不在其中 → 返回 0 篇并列出可用日期。

依赖:stdlib + pyyaml。系统 python3 即可。无需 API key。

用法:
  python3 fetch_arxiv.py --date 2026-06-15 --out ../.cache/listing_2026-06-15.json
  python3 fetch_arxiv.py --date today --limit 25     # 测试:只取前 25 篇
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
try:
    import yaml
except Exception:
    sys.stderr.write("缺少 pyyaml(系统 python3 应自带);pip install pyyaml\n"); sys.exit(2)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
API = "http://export.arxiv.org/api/query"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config", "config.yaml")
CACHE_DIR = os.path.join(SKILL_DIR, ".cache")
UA = {"User-Agent": "arxiv-scan/2.0"}
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

HDR_RE = re.compile(r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})\s*\(showing')
ID_RE = re.compile(r'arXiv:(\d{4}\.\d{4,5})')


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("categories", [])
    opts = cfg.setdefault("options", {}) or {}
    opts.setdefault("timezone", "UTC")
    cfg["options"] = opts
    return cfg


def resolve_date(s, tz):
    if not s or s.lower() in ("today", "今天"):
        now = datetime.now(tz) if tz else datetime.utcnow()
        return now.strftime("%Y-%m-%d")
    return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")


def http_get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_sections(html):
    """返回 [(date_str 'YYYY-MM-DD', [base_id...]), ...] 按页面顺序。"""
    headers = []
    for m in HDR_RE.finditer(html):
        d, mon, y = int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
        if mon:
            headers.append((m.start(), f"{y:04d}-{mon:02d}-{d:02d}"))
    ids = [(m.start(), m.group(1)) for m in ID_RE.finditer(html)]
    out = []
    for i, (pos, ds) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(html)
        out.append((ds, [bid for p, bid in ids if pos < p < end]))
    return out


def fetch_recent_ids(categories):
    """抓各板块 recent,合并成 {date_str: [ids(去重,保序)]}。"""
    by_date = {}
    for c in categories:
        url = f"https://arxiv.org/list/{c}/recent?skip=0&show=2000"
        try:
            html = http_get(url)
        except Exception as ex:
            sys.stderr.write(f"[列表抓取失败] {c}: {ex}\n"); continue
        secs = parse_sections(html)
        sys.stderr.write(f"[列表] {c}: 公告日 {[d for d,_ in secs]}\n")
        for ds, sec_ids in secs:
            lst = by_date.setdefault(ds, [])
            have = set(lst)
            for x in sec_ids:
                if x not in have:
                    lst.append(x); have.add(x)
        time.sleep(3)
    return by_date


def _parse_entries(raw):
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(ATOM + "entry"):
        idu = (e.findtext(ATOM + "id") or "").strip()
        m = re.search(r"/abs/(.+)$", idu)
        full = m.group(1) if m else idu
        base = re.sub(r"v\d+$", "", full)
        prim = e.find(ARXIV + "primary_category")
        abs_url = pdf_url = ""
        for link in e.findall(ATOM + "link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
            elif link.get("rel") == "alternate":
                abs_url = link.get("href", "")
        out.append({
            "id": base, "id_full": full,
            "title": " ".join((e.findtext(ATOM + "title") or "").split()),
            "abstract": " ".join((e.findtext(ATOM + "summary") or "").split()),
            "authors": [a.findtext(ATOM + "name") for a in e.findall(ATOM + "author")],
            "primary_category": prim.get("term") if prim is not None else "",
            "categories": [c.get("term") for c in e.findall(ATOM + "category")],
            "abs_url": abs_url or f"https://arxiv.org/abs/{full}",
            "pdf_url": pdf_url or f"https://arxiv.org/pdf/{full}",
            "html_url": f"https://arxiv.org/html/{full}",
            "published": e.findtext(ATOM + "published") or "",
        })
    return out


def fetch_metadata(ids):
    """用 API id_list 批量取元数据,返回按 ids 原序的论文列表。"""
    got = {}
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        params = urllib.parse.urlencode({"id_list": ",".join(batch), "max_results": len(batch)})
        try:
            for e in _parse_entries(http_get(f"{API}?{params}")):
                got[e["id"]] = e
        except Exception as ex:
            sys.stderr.write(f"[元数据抓取失败] batch {i//100}: {ex}\n")
        sys.stderr.write(f"[元数据] {min(i+len(batch),len(ids))}/{len(ids)}\n")
        time.sleep(3)
    return [got[x] for x in ids if x in got]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只取前 N 篇(测试用)")
    args = ap.parse_args()

    cfg = load_config(args.config); opts = cfg["options"]
    cats = list(cfg.get("categories") or [])
    if not cats:
        sys.stderr.write(f"[阻断] config.categories 为空,请先在 {args.config} 填板块。\n"); sys.exit(3)
    tz = None
    if ZoneInfo:
        try: tz = ZoneInfo(opts.get("timezone", "UTC"))
        except Exception: tz = ZoneInfo("UTC")
    target = resolve_date(args.date, tz)

    by_date = fetch_recent_ids(cats)
    avail = sorted(by_date.keys(), reverse=True)
    ids = by_date.get(target, [])
    sys.stderr.write(f"[目标] 公告日 {target} → {len(ids)} 个 id(recent 可用公告日: {avail})\n")
    if args.limit and ids:
        ids = ids[:args.limit]

    papers = fetch_metadata(ids) if ids else []
    result = {"date": target, "categories": cats, "available_dates": avail,
              "total": len(papers), "papers": papers}
    out = args.out or os.path.join(CACHE_DIR, f"listing_{target}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    sys.stderr.write(f"[结果] 公告日 {target} 抓到 {len(papers)} 篇 → {out}\n")
    print(out)


if __name__ == "__main__":
    main()
