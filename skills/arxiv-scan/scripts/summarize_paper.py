#!/usr/bin/env python3
"""
summarize_paper.py —— 用 DeepSeek 模型通读论文全文,给出【决策辅助型】中文总结。
目的:让用户看总结决定要不要自己精读,所以求"快、抓重点",不追求逐字绝对精确。

依赖:requests。key 从 DEEPSEEK_API_KEY 读(或 --api-key)。

用法:
  python3 summarize_paper.py --input ../.cache/ft_<id>.txt --out ../.cache/sum_<id>.md --title "..."
"""
import argparse, os, sys, time
try:
    import requests
except Exception:
    sys.stderr.write("缺少 requests\n"); sys.exit(2)

API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM = """你是文献速读助手。基于给出的【论文全文】写一份中文总结,供研究者快速判断这篇值不值得自己再精读。
重点是抓住核心、说清"做了什么/结论是什么/对我有没有用",不必逐字精确,但不要编造数字或结论。
按如下结构输出(简洁,不堆砌):
- **一句话**:做了什么 + 主要结论
- **方法/数据**:关键手段、设定、用到的数据或代码(有具体参数就点一下)
- **主要结果**:2-4 条,带上关键数字/趋势
- **与我的关联**:对读者的研究方向有什么用、可借鉴之处(说清这篇的适用对象/可迁移点)
- **是否建议我精读**:给"建议精读/可略读/可跳过"之一 + 一句理由

格式要求:直接从"**一句话**"开始,不要任何开场白或"好的/以下是"之类的话;小标题用加粗(**…**),不要用 # 或 ### 等标题符号(避免和外层标题撞层级)。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    ap.add_argument("--max-tokens", type=int, default=2500)
    ap.add_argument("--timeout", type=int, default=400)
    args = ap.parse_args()
    if not args.api_key:
        sys.stderr.write("[阻断] 无 DEEPSEEK_API_KEY\n"); sys.exit(3)

    body = open(args.input, encoding="utf-8").read()
    user = (f"论文标题:{args.title}\n\n" if args.title else "") + \
        "下面是论文全文(已剥离参考文献,保留 LaTeX 公式与章节标题):\n\n" + \
        "===== 全文开始 =====\n" + body + "\n===== 全文结束 ====="

    payload = {"model": args.model, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user}],
        "temperature": 0.3, "max_tokens": args.max_tokens, "stream": False}
    t0 = time.time()
    try:
        r = requests.post(API_URL, headers={"Authorization": f"Bearer {args.api_key}",
                          "Content-Type": "application/json"}, json=payload, timeout=args.timeout)
    except Exception as ex:
        sys.stderr.write(f"[网络错误] {ex}\n"); sys.exit(1)
    if r.status_code != 200:
        sys.stderr.write(f"[API错误] HTTP {r.status_code}: {r.text[:300]}\n"); sys.exit(1)
    data = r.json(); content = data["choices"][0]["message"].get("content", "") or ""
    usage = data.get("usage", {})
    # 清掉模型偶尔加的开场白 / 顶部分隔线
    lines = content.splitlines()
    while lines and (not lines[0].strip() or lines[0].strip() == "---"
                     or lines[0].lstrip("#* ").startswith(("好的", "以下", "这是", "如下"))):
        lines.pop(0)
    content = "\n".join(lines).strip()
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    sys.stderr.write(f"[总结完成] {args.out} | {time.time()-t0:.1f}s | "
                     f"in {usage.get('prompt_tokens','?')} out {usage.get('completion_tokens','?')}\n")
    print(args.out)


if __name__ == "__main__":
    main()
