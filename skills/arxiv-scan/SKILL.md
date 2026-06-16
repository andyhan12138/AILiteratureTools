---
name: arxiv-scan
description: >-
  刷 arxiv 并用模型初筛:拉取指定板块【当日】更新的【全部】论文(不走 arxiv 关键词搜索),
  用 DeepSeek 模型对每篇按「我的研究方向 prose」判四档(非常相关/相关/不相关/完全不相关)+ 一句话;
  前两档(非常相关+相关)再通读全文做总结;最终整合成每日一份文档——含【通过初筛的全文总结】+【被筛掉的题目+一句话】。
  典型触发:「刷一下今天的 arxiv」「跑 arxiv 初筛 6月11日」「arxiv 日报(模型筛)」。
  研究方向写在 skills/arxiv-scan/config/interest.md,板块/通过档位在 config/config.yaml。
model: sonnet
argument-hint: "[日期 YYYY-MM-DD,默认今天]"
---

# 刷 arxiv + 模型初筛 (arxiv-scan)

一条命令:**当日该板块全部论文 → 模型逐篇判四档+一句话 → 前两档的全文总结 → 整合日报**。
筛选**完全交给模型**(不用 arxiv 关键词搜索、也不在本地做关键词匹配)。
编排用 Sonnet 即可;打分/总结由 DeepSeek-V4-flash(便宜)在脚本里完成。

路径以**本仓库根目录**(`scan.sh` 所在处)为基准。

## 0. 前置

- **研究方向**:`skills/arxiv-scan/config/interest.md` —— 这是判档的**唯一依据**,必须先填(模板未填会被阻断)。按「非常相关 / 相关 / 不相关 / 完全不相关」描述,反例尤其重要。
- **板块/通过档位/模型**:`skills/arxiv-scan/config/config.yaml`(categories、pass_levels、max_summarize、batch_size 等)。
- **API key**:需要环境变量 `DEEPSEEK_API_KEY`。
  - 它若写在 `~/.zshrc`,只对交互式 shell 可见 → 运行时**用 `zsh -ic '...'` 包一层**(让脚本从环境读到)。
  - 或把 key 放进 `.claude/settings.local.json` 的 `env`,则可直接运行、无需 `zsh -ic`。

## 1. 跑(一条命令)

```
zsh -ic 'python3 skills/arxiv-scan/scripts/arxiv_scan.py --date <日期>'
```
- `--date` 默认今天;可写 `2026-06-11`。
- 测试省钱:加 `--limit 25`(只抓前 25 篇);`--max-summarize N` 临时压低总结数。
- 中断后续跑:加 `--reuse`(复用 `.cache` 里已完成的抓取/打分)。
- 产物:`notes/arxiv_daily/<YYYYMM>/<DD>_scan.md`,stderr 有分步进度(含档位分布)。

跑完后向用户汇报:抓取 N 篇、各档位分布、通过(前两档)M 篇、日报路径;若有"全文未获取(降级摘要)"的篇,点名。

## 2. 日报结构(脚本自动生成)

```
## 通过初筛 · 全文总结(M 篇)
### [非常相关] <标题>     ← 档位
- arxiv / HTML 链接, 作者, 主类目
<决策辅助型总结:一句话/方法数据/主要结果/与我的关联/是否建议精读>

## 初筛掉 · 题目 + 一句话(其余,按档位降序)
| 档 | 标题 | 一句话(据摘要) |
```
→ 你读"通过初筛"的总结,决定哪些值得**自己人工精读**;被筛掉的也留了题目+一句话,不会静默丢弃。

## 分步脚本(可单独跑/调试)

- `fetch_arxiv.py --date D [--limit N]` → 当日全部论文 JSON(纯 stdlib)。
- `score_papers.py --listing X.json` → 加上 level/one_line 的 JSON(DeepSeek 批量判四档)。
- `fetch_fulltext.py <id>` → arxiv HTML 全文清洗文本(留公式、剥参考文献)。
- `summarize_paper.py --input ft.txt --title "..."` → 单篇全文总结。
- `arxiv_scan.py` → 把上面串起来 + 整合日报。

## 边界与维护

- **成本**:判档 ~50-90 篇/天用 v4-flash 很便宜;全文总结由 `max_summarize` 封顶(防某天命中过多烧钱;超出的篇按档位从高到低截断,仍进表格)。
- **改方向**:只动 `interest.md`;改板块/通过档位(pass_levels)只动 `config.yaml`。
  - 想更严 → `pass_levels: [非常相关]`;更宽 → 把"不相关"也加进去。
- **公式保真**:arxiv 论文走 HTML(留 LaTeX);本地 PDF / 非 arxiv 是 Phase 2,本工具暂只做 arxiv。
- **与 arxiv-daily 的关系**:arxiv-daily 是早期的"关键词 OR"版;本工具用模型四档初筛取代关键词,是日扫的新版。
- **要逐字核验某篇**(防编造)→ 那是另一回事,用 `paper-read-bench`。
- 中间产物在 `skills/arxiv-scan/.cache/`,可清理。
