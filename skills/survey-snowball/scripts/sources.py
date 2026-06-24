#!/usr/bin/env python3
"""
sources.py —— 引文图的数据源客户端:INSPIRE-HEP(免 key) + NASA ADS(需 ADS_DEV_KEY)
             + Google Scholar 搜索(经 SerpApi,需 SERPAPI_API_KEY)。

对每个数据源提供统一的三件事:
  - resolve(identifier)        把 arxiv/doi/recid/bibcode/标题 解析成本库的种子记录
  - backward(node)             取该节点的【参考文献】(它引的)
  - forward(node)              取【施引该节点的论文】(引它的),按被引降序
Scholar 额外提供 search(query),可直接做 Google Scholar 搜索。

返回的记录统一成一个 dict(见 normalize / blank_rec),关键字段:
  key(规范去重键) title year doi arxiv recid bibcode citation_count authors abstract sources

设计:
  - ADS 的 references()/citations() 一次分页查询就带回完整元数据(含 abstract),最省调用 → 优先。
  - Google Scholar 无官方公开 API;这里仅通过 SerpApi 做搜索/施引补充,不直接抓 scholar.google.com。
  - INSPIRE 的参考文献内嵌在记录里(无完整元数据)→ 收集 recid/arxiv 后再批量解析(同次带回 abstract)。
  - 任一源缺失(无 token / 网络错误)只告警、跳过,不让整条流水线崩。

纯 requests(系统 python3 自带);无任何个人路径,token 走环境变量,可直接分享。
"""
import os, re, sys, time, json

try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)

UA = "survey-snowball/1.0 (literature toolchain; mailto via ADS token only)"
INSPIRE_API = "https://inspirehep.net/api/literature"
ADS_API = "https://api.adsabs.harvard.edu/v1/search/query"
SERPAPI_API = "https://serpapi.com/search.json"

ARXIV_RE = re.compile(r"^\s*(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?\s*$", re.I)
OLD_ARXIV_RE = re.compile(r"^\s*(?:arxiv:)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\s*$", re.I)
DOI_RE = re.compile(r"^\s*(?:doi:)?(10\.\d{4,9}/\S+)\s*$", re.I)
RECID_RE = re.compile(r"^\s*(?:recid:|inspire:)?(\d{3,9})\s*$", re.I)


# ----------------------------- 通用记录 -----------------------------

def blank_rec():
    return {"key": None, "title": "", "year": None, "doi": None, "arxiv": None,
            "recid": None, "bibcode": None, "citation_count": 0, "authors": [],
            "abstract": "", "url": None, "scholar_result_id": None,
            "scholar_cites_id": None, "scholar_cluster_id": None, "sources": []}


def norm_arxiv(a):
    if not a:
        return None
    a = str(a).strip()
    m = ARXIV_RE.match(a) or OLD_ARXIV_RE.match(a)
    return m.group(1) if m else re.sub(r"v\d+$", "", a.replace("arXiv:", "").replace("arxiv:", "").strip())


def norm_doi(d):
    return str(d).strip().lower().rstrip(".") if d else None


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def canonical_key(rec):
    """规范去重键:DOI > arXiv > inspire recid > ads bibcode > 归一标题 > Scholar ID。"""
    if rec.get("doi"):
        return "doi:" + norm_doi(rec["doi"])
    if rec.get("arxiv"):
        return "arxiv:" + norm_arxiv(rec["arxiv"])
    if rec.get("recid"):
        return "inspire:" + str(rec["recid"])
    if rec.get("bibcode"):
        return "ads:" + str(rec["bibcode"])
    nt = norm_title(rec.get("title"))
    if nt:
        return "title:" + nt
    if rec.get("scholar_result_id"):
        return "scholar:" + str(rec["scholar_result_id"])
    return None


def classify_id(s):
    """判断用户给的种子标识属于哪种。返回 (kind, value)。"""
    s = s.strip()
    if ARXIV_RE.match(s) or OLD_ARXIV_RE.match(s):
        return "arxiv", norm_arxiv(s)
    if DOI_RE.match(s):
        return "doi", DOI_RE.match(s).group(1).rstrip(".")
    # bibcode: 19 字符,形如 2021PhRvD.103l3015H
    if re.match(r"^\d{4}[A-Za-z.&]{5}[\w.]{9}[A-Z.]$", s) and len(s) == 19:
        return "bibcode", s
    if RECID_RE.match(s):
        return "recid", RECID_RE.match(s).group(1)
    return "title", s


# ----------------------------- INSPIRE -----------------------------

class Inspire:
    name = "inspire"
    enabled = True

    def __init__(self, pause=0.4):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        self.pause = pause
        self.fields = ("control_number,titles,arxiv_eprints,dois,citation_count,"
                       "authors,earliest_date,abstracts")

    def _get(self, params, timeout=40):
        for attempt in range(3):
            try:
                r = self.s.get(INSPIRE_API, params=params, timeout=timeout)
                if r.status_code == 429:
                    time.sleep(2 + 2 * attempt); continue
                if r.status_code != 200:
                    sys.stderr.write(f"  [inspire HTTP {r.status_code}]\n"); return None
                time.sleep(self.pause)
                return r.json()
            except Exception as ex:
                sys.stderr.write(f"  [inspire 网络错误 attempt={attempt}] {ex}\n")
                time.sleep(1 + attempt)
        return None

    def ping(self):
        """轻量联网探测。能拿到 JSON 响应即认为该源可用。"""
        data = self._get({"fields": "control_number", "size": 1}, timeout=10)
        return data is not None

    def _parse_hit(self, md):
        rec = blank_rec(); rec["sources"] = ["inspire"]
        rec["recid"] = md.get("control_number")
        ts = md.get("titles") or []
        rec["title"] = (ts[0].get("title") if ts else "") or ""
        eps = md.get("arxiv_eprints") or []
        rec["arxiv"] = norm_arxiv(eps[0]["value"]) if eps else None
        dois = md.get("dois") or []
        rec["doi"] = norm_doi(dois[0]["value"]) if dois else None
        rec["citation_count"] = md.get("citation_count") or 0
        ed = md.get("earliest_date") or ""
        rec["year"] = int(ed[:4]) if ed[:4].isdigit() else None
        aus = md.get("authors") or []
        rec["authors"] = [a.get("full_name", "") for a in aus[:6]]
        abs_ = md.get("abstracts") or []
        rec["abstract"] = (abs_[0].get("value") if abs_ else "") or ""
        rec["key"] = canonical_key(rec)
        return rec

    def resolve(self, kind, value):
        if kind == "arxiv":
            q = f"arxiv {value}"
        elif kind == "doi":
            q = f"doi {value}"
        elif kind == "recid":
            q = f"control_number {value}"
        else:  # bibcode 不是 INSPIRE 原生键 → 退化为标题/全文检索
            q = value
        data = self._get({"q": q, "fields": self.fields, "size": 1})
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        return self._parse_hit(hits[0]["metadata"]) if hits else None

    def _resolve_recids(self, recids):
        """批量把 recid 列表解析成完整记录(每块 <=80)。"""
        out = {}
        recids = [r for r in recids if r]
        for i in range(0, len(recids), 80):
            chunk = recids[i:i + 80]
            q = " or ".join(f"control_number {r}" for r in chunk)
            data = self._get({"q": q, "fields": self.fields, "size": len(chunk)})
            for h in ((data or {}).get("hits") or {}).get("hits") or []:
                rec = self._parse_hit(h["metadata"])
                if rec.get("recid"):
                    out[rec["recid"]] = rec
        return out

    def backward(self, node, cap):
        """node 的参考文献。先从记录里抠 reference 的 recid,再批量解析元数据。"""
        recid = node.get("recid")
        if not recid:
            return []
        data = self._get({"q": f"control_number {recid}",
                           "fields": "references", "size": 1})
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        if not hits:
            return []
        refs = (hits[0]["metadata"].get("references") or [])
        recids = []
        for rf in refs:
            link = (rf.get("record") or {}).get("$ref") or ""
            m = re.search(r"/literature/(\d+)", link)
            if m:
                recids.append(m.group(1))
        # 截断在解析前:reference 自身无被引数,这里按出现顺序取前 cap*2 个有 recid 的,
        # 解析后再按 citation 排序截断(在 build_graph 里统一截)。
        resolved = self._resolve_recids(recids[: max(cap * 2, cap)])
        return list(resolved.values())

    def forward(self, node, cap):
        """施引该节点的论文,按被引降序取前 cap。"""
        recid = node.get("recid")
        if not recid:
            return []
        size = min(cap, 1000)
        data = self._get({"q": f"refersto recid {recid}", "fields": self.fields,
                           "sort": "mostcited", "size": size})
        out = []
        for h in ((data or {}).get("hits") or {}).get("hits") or []:
            out.append(self._parse_hit(h["metadata"]))
        return out


# ------------------------------- ADS -------------------------------

class Ads:
    name = "ads"

    def __init__(self, token, pause=0.5):
        self.token = token
        self.enabled = bool(token)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Authorization": f"Bearer {token}"})
        self.pause = pause
        self.fl = "bibcode,title,citation_count,year,doi,identifier,abstract,author"

    def _get(self, params, timeout=40):
        for attempt in range(3):
            try:
                r = self.s.get(ADS_API, params=params, timeout=timeout)
                rem = r.headers.get("X-RateLimit-Remaining")
                if r.status_code == 429:
                    sys.stderr.write("  [ads 限流,等待…]\n"); time.sleep(5 + 3 * attempt); continue
                if r.status_code in (401, 403):
                    sys.stderr.write("  [ads 鉴权失败:检查 ADS_DEV_KEY]\n"); self.enabled = False; return None
                if r.status_code != 200:
                    sys.stderr.write(f"  [ads HTTP {r.status_code}]\n"); return None
                if rem is not None and rem.isdigit() and int(rem) < 20:
                    sys.stderr.write(f"  [ads 今日剩余配额 {rem},注意]\n")
                time.sleep(self.pause)
                return r.json()
            except Exception as ex:
                sys.stderr.write(f"  [ads 网络错误 attempt={attempt}] {ex}\n")
                time.sleep(1 + attempt)
        return None

    def ping(self):
        """轻量联网探测。无 token 或鉴权失败都会返回 False。"""
        if not self.enabled:
            return False
        data = self._get({"q": "*:*", "fl": "bibcode", "rows": 0}, timeout=10)
        return data is not None

    def _parse_doc(self, d):
        rec = blank_rec(); rec["sources"] = ["ads"]
        rec["bibcode"] = d.get("bibcode")
        t = d.get("title") or []
        rec["title"] = (t[0] if isinstance(t, list) and t else (t or "")) or ""
        rec["citation_count"] = d.get("citation_count") or 0
        y = d.get("year")
        rec["year"] = int(y) if y and str(y).isdigit() else None
        rec["doi"] = norm_doi((d.get("doi") or [None])[0])
        for ident in d.get("identifier") or []:
            if ARXIV_RE.match(ident) or OLD_ARXIV_RE.match(ident) or ident.lower().startswith("arxiv:"):
                rec["arxiv"] = norm_arxiv(ident); break
        rec["authors"] = (d.get("author") or [])[:6]
        rec["abstract"] = d.get("abstract") or ""
        rec["key"] = canonical_key(rec)
        return rec

    def _query(self, q, rows, sort=None):
        params = {"q": q, "fl": self.fl, "rows": rows}
        if sort:
            params["sort"] = sort
        data = self._get(params)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        return [self._parse_doc(d) for d in docs]

    def resolve(self, kind, value):
        if kind == "arxiv":
            q = f'arxiv:"{value}"'
        elif kind == "doi":
            q = f'doi:"{value}"'
        elif kind == "bibcode":
            q = f'bibcode:"{value}"'
        elif kind == "recid":
            return None  # recid 是 INSPIRE 的键,ADS 无
        else:
            q = f'title:"{value}"'
        hits = self._query(q, rows=1)
        return hits[0] if hits else None

    def backward(self, node, cap):
        bib = node.get("bibcode")
        if not bib:
            return []
        return self._query(f"references(bibcode:{bib})", rows=min(cap, 2000),
                           sort="citation_count desc")

    def forward(self, node, cap):
        bib = node.get("bibcode")
        if not bib:
            return []
        return self._query(f"citations(bibcode:{bib})", rows=min(cap, 2000),
                           sort="citation_count desc")


# -------------------------- Google Scholar --------------------------

class Scholar:
    """Google Scholar 搜索源。通过 SerpApi 调用,不直接抓 scholar.google.com。"""
    name = "scholar"

    def __init__(self, token, pause=0.5):
        self.token = token
        self.enabled = bool(token)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.pause = pause

    def _get(self, params, timeout=40):
        if not self.enabled:
            return None
        params = dict(params)
        params.setdefault("engine", "google_scholar")
        params["api_key"] = self.token
        for attempt in range(3):
            try:
                r = self.s.get(SERPAPI_API, params=params, timeout=timeout)
                if r.status_code == 429:
                    sys.stderr.write("  [scholar/serpapi 限流,等待…]\n")
                    time.sleep(5 + 3 * attempt)
                    continue
                if r.status_code in (401, 403):
                    sys.stderr.write("  [scholar/serpapi 鉴权失败:检查 SERPAPI_API_KEY]\n")
                    self.enabled = False
                    return None
                if r.status_code != 200:
                    sys.stderr.write(f"  [scholar/serpapi HTTP {r.status_code}]\n")
                    return None
                data = r.json()
                if data.get("error"):
                    sys.stderr.write(f"  [scholar/serpapi 错误] {data['error']}\n")
                    return None
                time.sleep(self.pause)
                return data
            except Exception as ex:
                sys.stderr.write(f"  [scholar/serpapi 网络错误 attempt={attempt}] {ex}\n")
                time.sleep(1 + attempt)
        return None

    def ping(self):
        """轻量联网探测。注意:SerpApi 侧通常会计一次查询。"""
        data = self._get({"q": "test", "num": 1}, timeout=10)
        return data is not None

    def _parse_result(self, r, force_kind=None, force_value=None):
        rec = blank_rec()
        rec["sources"] = ["scholar"]
        rec["title"] = r.get("title") or ""
        rec["url"] = r.get("link") or None
        rec["scholar_result_id"] = r.get("result_id")
        rec["abstract"] = r.get("snippet") or ""

        pub = r.get("publication_info") or {}
        summary = pub.get("summary") or ""
        if summary:
            ym = re.search(r"\b(?:19|20)\d{2}\b", summary)
            if ym:
                rec["year"] = int(ym.group(0))
            authors_part = summary.split(" - ")[0]
            rec["authors"] = [a.strip() for a in authors_part.split(",") if a.strip()][:6]

        inline = r.get("inline_links") or {}
        cited = inline.get("cited_by") or {}
        rec["citation_count"] = cited.get("total") or 0
        rec["scholar_cites_id"] = cited.get("cites_id")
        versions = inline.get("versions") or {}
        rec["scholar_cluster_id"] = versions.get("cluster_id")

        if force_kind == "arxiv":
            rec["arxiv"] = norm_arxiv(force_value)
        elif force_kind == "doi":
            rec["doi"] = norm_doi(force_value)
        elif force_kind == "bibcode":
            rec["bibcode"] = force_value

        rec["key"] = canonical_key(rec)
        return rec

    def search(self, query, rows=10, cites_id=None):
        params = {"num": max(1, min(int(rows or 10), 20))}
        if cites_id:
            params["cites"] = str(cites_id)
            if query:
                params["q"] = query
        else:
            params["q"] = query
        data = self._get(params)
        return [self._parse_result(r) for r in (data or {}).get("organic_results", [])]

    def resolve(self, kind, value):
        if kind == "arxiv":
            q = f'arxiv "{value}"'
        elif kind == "doi":
            q = f'"{value}"'
        elif kind == "bibcode":
            q = f'"{value}"'
        elif kind == "recid":
            return None
        else:
            q = f'"{value}"'
        data = self._get({"q": q, "num": 1})
        hits = (data or {}).get("organic_results") or []
        return self._parse_result(hits[0], force_kind=kind, force_value=value) if hits else None

    def backward(self, node, cap):
        # Google Scholar 搜索结果不提供参考文献列表;这里只作为搜索/施引补充源。
        return []

    def forward(self, node, cap):
        cites_id = node.get("scholar_cites_id")
        if not cites_id:
            return []
        return self.search("", rows=min(cap, 20), cites_id=cites_id)


# --------------------------- 工厂 + 自测 ---------------------------

def build_clients(pause=None):
    """按环境变量装配可用数据源。每个源先联网探测,能连上才加入。"""
    inspire_pause = 0.4 if pause is None else pause
    ads_pause = 0.5 if pause is None else pause
    scholar_pause = 0.5 if pause is None else pause
    clients = []
    serpapi_key = (os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERP_API_KEY") or "").strip()
    candidates = [
        Inspire(pause=inspire_pause),
        Ads(os.environ.get("ADS_DEV_KEY", "").strip(), pause=ads_pause),
        Scholar(serpapi_key, pause=scholar_pause),
    ]
    for c in candidates:
        if not c.enabled:
            if c.name == "ads":
                sys.stderr.write("  [提示] 未检测到 ADS_DEV_KEY → 跳过 ADS(astro 覆盖会变弱)。\n")
            elif c.name == "scholar":
                sys.stderr.write("  [提示] 未检测到 SERPAPI_API_KEY → 跳过 Google Scholar 搜索源。\n")
            continue
        try:
            ok = c.ping()
        except Exception as ex:
            sys.stderr.write(f"  [{c.name} 联网探测失败] {ex}\n")
            ok = False
        if ok:
            clients.append(c)
            sys.stderr.write(f"  [{c.name} 可用]\n")
        else:
            sys.stderr.write(f"  [{c.name} 不可用,已跳过]\n")
    if not clients:
        sys.stderr.write("  [警告] 没有可用数据源。\n")
    return clients


if __name__ == "__main__":
    # 烟雾测试:python3 sources.py <arxiv/doi/recid/bibcode/标题>
    seed = sys.argv[1] if len(sys.argv) > 1 else "2504.08514"
    kind, val = classify_id(seed)
    sys.stderr.write(f"[smoke] 种子={seed} → kind={kind} val={val}\n")
    for c in build_clients():
        rec = c.resolve(kind, val)
        sys.stderr.write(f"[{c.name}] resolve → " + (json.dumps({k: rec[k] for k in
            ('title', 'recid', 'bibcode', 'arxiv', 'doi', 'citation_count')},
            ensure_ascii=False) if rec else "None") + "\n")
        if rec:
            bw = c.backward(rec, 50)
            fw = c.forward(rec, 50)
            sys.stderr.write(f"[{c.name}] 参考文献 {len(bw)} 篇 · 施引 {len(fw)} 篇\n")
