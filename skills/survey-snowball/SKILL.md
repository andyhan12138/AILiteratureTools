---
name: survey-snowball
description: >-
  引文图驱动的专题调研(雪球式):从一篇或多篇【种子论文】出发,沿 INSPIRE+ADS 抓两类邻居——
  ① 后向引文闭包(参考文献、参考文献的参考文献,深度可调,默认2)② 前向邻域(施引论文 + 施引者的参考文献);
  合并去重 → 用 DeepSeek 按 title 对【调研主题】判四档初筛 → 入选的抓 abstract 出逐篇证据卡片(支持/证伪)→
  DeepSeek-V4-pro 综述成三轴报告(前人做了什么 / 可行性 / 突破口)+ must-read 短名单。
  用来完整摸清一个思路:前人做到哪、可行性、突破口在哪。
  典型触发:「雪球调研一下 <某篇>:<某思路> 的可行性」「以这几篇为种子做引文图综述」。
  种子/主题/参数在命令行;板块无关(纯靠引文图,不靠关键词搜索)。
model: sonnet
argument-hint: "[种子 arxiv/doi/recid/bibcode/标题 …] [--topic \"本次思路\"]"
---

# 引文图专题调研 (survey-snowball)

从种子论文出发,**雪球式**铺开引文图,筛出与你思路相关的工作,综述成「前人做了什么 / 可行性 / 突破口」。
编排用 Sonnet 即可;打分、卡片、综述全在脚本里走 DeepSeek(flash 初筛 + V4-pro 综述)。
路径以**本仓库根目录**(`snowball.sh` 所在处)为基准。

## 0. 前置

- **API key**:`DEEPSEEK_API_KEY`(初筛/卡片/综述都要);`ADS_DEV_KEY`(可选,缺则只用 INSPIRE,astro 覆盖变弱)。
  - 写在 `~/.zshrc` → 运行时用 `zsh -ic '...'` 包一层;或放进 `.claude/settings.local.json` 的 `env`。
- **相关性标尺**(title 初筛的依据)三块按需组合,至少一项:
  - **(a) 本次思路** `--topic "…"` —— 最贴合"调研某个具体思路",**推荐总是给**。
  - **(b) 长期画像** `--use-interest` —— 并入 `config/interest.md`(没填会自动忽略;可 `--interest-file` 指向 arxiv-scan 那份)。
  - **(c) 种子主题** —— 默认就会用种子的标题+摘要当锚;`--no-seed` 关闭。

## 1. 跑(一条命令)

```
zsh -ic './snowball.sh "<种子>" [更多种子…] --topic "<本次思路>" [--use-interest] [--depth 2] [--no-forward] [--reuse]'
```
- **种子**:arxiv id / DOI / INSPIRE recid / ADS bibcode / 直接标题,**可多篇**(图取并集);开头连续的非 `--` 参数都当种子。
- 也可直接调脚本:`python3 skills/survey-snowball/scripts/snowball.py --seeds A B --topic "…"`。
- `--depth` 后向闭包深度(默认 2);`--no-forward` 只做后向;`--reuse` 复用 `.cache` 已完成步骤;`--analyze-cap N` 临时改 M。
- 产物:`notes/snowball/<name>/` —— `report.md`(三轴综述)+ `cards.md`(逐篇卡片)+ `candidates.md`(可按相关性/被引两种排序)+ `data.json`。

跑完向用户汇报:种子解析情况、候选池规模 + **层分布**、入选 M 篇 + 立场分布、报告路径;ADS 缺席要点名(只跑了 INSPIRE)。

## 2. 流水线(脚本自动串)

1. `build_graph.py` 解析种子 → 后向闭包(深度 D)+ 前向邻域(施引 + 施引者参考文献)→ 跨源去重 → **截断**。
2. `score_titles.py` DeepSeek-flash 按 title 判四档(非常相关/相关/不相关/完全不相关),拿不准倾向"相关"。
3. 选入选 ≤M:`pass_levels` 内,排序 **相关性档 → 第一层优先 → 被引降序**,封顶 M。
4. `make_cards.py` 抓 abstract + flash 出逐篇卡片(问题/方法/结果/对本思路立场)。
5. `synthesize.py` DeepSeek-V4-pro 出三轴综述 + must-read;正文引用用 `arXiv:`/`DOI:`/作者年份 可检索标记。

## 3. 爆炸控制 / 优先级(重要)

- 单节点 fan-out 上限 **K=200**(任一方向超过按被引降序保留前 K);前向只对前 **150** 个高被引施引者再取参考文献。
- 候选池总上限 **N=3000**,入选上限 **M=40**。
- **第一层(深度1)优先于第二层(深度2)**:池截断按「深度→被引」;入选截断按「相关性→深度→被引」,确保第一层不被体量更大的第二层挤掉。层分布会打印出来核对。
- 全部数字在 `config/config.yaml` 可调。

## 4. 边界与维护

- **不靠关键词搜索**,纯引文图;覆盖取决于 INSPIRE(gr-qc/nucl-th 强)+ ADS(astro 强)。
- **立场(支持/证伪)是 flash 初筛的粗线索**,不当定论;最终以人工读 abstract / 全文为准。
- 调参只动 `config.yaml`;改长期画像只动 `config/interest.md`。中间产物在 `skills/survey-snowball/.cache/`,可清。
- 想对入选某篇深读/推公式 → 用 `read-assist`;想逐字核验防编造 → `paper-read-bench`。
- 与 `survey-search`(关键词/主题检索式调研)互补:那个按主题广撒网,这个按引文图深挖一个思路。
- **可分享**:脚本 `__file__` 自定位、启动壳相对路径、key 全走环境变量、个人内容只在 `interest.md`。分享前清 `.cache/`、换掉 `interest.md`。见 `README.md`。
