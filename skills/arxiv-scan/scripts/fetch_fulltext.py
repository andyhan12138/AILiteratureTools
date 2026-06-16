#!/usr/bin/env python3
"""
fetch_fulltext.py —— 给定 arxiv id,下载 HTML 全文并清洗成纯文本(尽量保留 LaTeX 公式),
写到缓存文件供主流程 Read 全文精读。纯 stdlib。

依次尝试:
  1) https://arxiv.org/html/<id>      (arxiv 官方 LaTeXML HTML,新论文多有)
  2) https://ar5iv.org/abs/<id>       (ar5iv 镜像,老论文兜底)
  3) https://ar5iv.labs.arxiv.org/html/<id>

用法:
  python3 fetch_fulltext.py 2506.09283v1
  python3 fetch_fulltext.py 2506.09283 --out /tmp/2506.09283.txt --max-chars 300000

成功:打印 "OK <path> <chars> <url>" 到 stdout,退出码 0。
失败:打印 "FAIL <reason>" 到 stderr,退出码 1(主流程应改用 WebFetch 或退回摘要)。
"""
import argparse
import html
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SKILL_DIR, ".cache")

SKIP_TAGS = {"script", "style", "nav", "header", "footer"}
BLOCK_TAGS = {"p", "div", "section", "li", "ul", "ol", "table", "tr",
              "blockquote", "article"}
HEAD_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.in_math = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "math":
            self.in_math = True
            alt = dict(attrs).get("alttext")
            if alt:
                self.parts.append(" $" + html.unescape(alt) + "$ ")
            return
        if self.in_math:
            return
        if tag in HEAD_TAGS:
            self.parts.append("\n\n#### ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "math":
            self.in_math = False
            return
        if tag in BLOCK_TAGS or tag in HEAD_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth or self.in_math:
            return
        self.parts.append(data)


# ar5iv / arxiv-HTML 的界面噪声行(整行匹配即丢弃)
NOISE_LINES = {
    "title:", "content selection saved. describe the issue below:",
    "description:", "license: arxiv.org perpetual non-exclusive license",
    "beta", "id", "report issue for preceding element",
}
# 参考文献段起始标题:从这里(含)往后整段切掉,精读用不到且极占 token
REF_HEAD = re.compile(r"^#*\s*(references|bibliography|references and notes)\s*$",
                      re.IGNORECASE)


def strip_noise(text, drop_refs=True):
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if drop_refs and REF_HEAD.match(s):
            out.append("\n[参考文献段已省略]")
            break
        if s.lower() in NOISE_LINES:
            continue
        out.append(ln)
    return "\n".join(out)


def clean_html(raw_bytes, drop_refs=True):
    raw = raw_bytes.decode("utf-8", errors="replace")
    p = Extractor()
    p.feed(raw)
    text = "".join(p.parts)
    # 行内多空白折叠、行尾去空白、最多保留一个空行
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = strip_noise(text, drop_refs=drop_refs)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def try_fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-daily-skill/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arxiv_id", help="如 2506.09283 或 2506.09283v1")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-chars", type=int, default=0, help=">0 时截断,0=不截断")
    args = ap.parse_args()

    aid = args.arxiv_id.strip()
    base = re.sub(r"v\d+$", "", aid)
    candidates = [
        f"https://arxiv.org/html/{aid}",
        f"https://ar5iv.org/abs/{base}",
        f"https://ar5iv.labs.arxiv.org/html/{base}",
    ]

    text = None
    used = None
    last_err = ""
    for url in candidates:
        try:
            raw = try_fetch(url)
            cleaned = clean_html(raw)
            if len(cleaned) < 800:  # 太短多半是错误页/未渲染
                last_err = f"{url} 内容过短({len(cleaned)}字符)"
                continue
            text, used = cleaned, url
            break
        except Exception as ex:
            last_err = f"{url}: {ex}"
            continue

    if text is None:
        sys.stderr.write(f"FAIL {aid}: {last_err}\n")
        sys.exit(1)

    if args.max_chars and len(text) > args.max_chars:
        text = text[:args.max_chars] + "\n\n[...truncated...]"

    out = args.out
    if not out:
        os.makedirs(CACHE_DIR, exist_ok=True)
        out = os.path.join(CACHE_DIR, f"{base}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# source: {used}\n# arxiv_id: {aid}\n\n{text}\n")

    print(f"OK {out} {len(text)} {used}")


if __name__ == "__main__":
    main()
