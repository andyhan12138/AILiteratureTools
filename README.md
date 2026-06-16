# AI Literature Tools

面向物理 / 天体物理科研的两个文献自动化工具,用 LLM(DeepSeek)+ 学术数据库(arXiv / INSPIRE-HEP / NASA ADS)做**每日 arxiv 初筛**和**引文图专题调研**。

两者都既是 [Claude Code](https://claude.com/claude-code) 技能(`skills/<name>/SKILL.md`),也能脱离 Claude、直接用根目录的启动壳跑——底层全是纯 Python 管线。

## 工具

### 1. `arxiv-scan` —— 每日 arxiv 模型初筛
拉取指定板块**当日**更新的全部论文(不走关键词搜索),用 DeepSeek 按你的研究方向逐篇判四档(非常相关/相关/不相关/完全不相关)+ 一句话;前两档再通读全文做总结,整合成每日一份 markdown。

```bash
./scan.sh              # 扫今天
./scan.sh 2026-06-11   # 扫指定日期
```
- 先填 `skills/arxiv-scan/config/interest.md`(你的研究方向,初筛的唯一依据,**不填会被阻断**)。
- 板块/通过档位/模型在 `skills/arxiv-scan/config/config.yaml`。

### 2. `survey-snowball` —— 引文图驱动的专题调研
从一篇或多篇**种子论文**出发,沿 INSPIRE+ADS 抓后向引文闭包(参考文献的参考文献,深度可调)+ 前向邻域(施引论文及其参考文献);去重 → DeepSeek 按 title 初筛 → 入选者抓 abstract 出逐篇证据卡片 → DeepSeek-V4-pro 综述成三轴报告(前人做了什么 / 可行性 / 突破口)+ must-read 短名单。

```bash
./snowball.sh "2504.08514" --topic "用准万有关系约束中子星状态方程的可行性"
./snowball.sh "<arxiv/doi/recid/bibcode/标题>" [更多种子…] [--use-interest] [--depth 2]
```
- 详见 `skills/survey-snowball/README.md`。参数在 `skills/survey-snowball/config/config.yaml`。

## 前置

- **Python 3** + 依赖:`pip install requests pyyaml`
- **API key(环境变量)**:
  - `DEEPSEEK_API_KEY` —— 两个工具都要(打分/总结/卡片/综述)。
  - `ADS_DEV_KEY` —— 仅 `survey-snowball` 可选;无则只用 INSPIRE 单源(astro 覆盖变弱)。申请:`ui.adsabs.harvard.edu` → Account → API Token。
  - key 若写在 `~/.zshrc`(只对交互式 shell 可见),运行时用 `zsh -ic './scan.sh …'` 包一层,或写进环境。

## 目录结构

```
AILiteratureTools/
├── scan.sh                      # arxiv-scan 启动壳
├── snowball.sh                  # survey-snowball 启动壳
└── skills/
    ├── arxiv-scan/
    │   ├── SKILL.md
    │   ├── config/{config.yaml, interest.md}
    │   └── scripts/{fetch_arxiv, score_papers, fetch_fulltext, summarize_paper, arxiv_scan}.py
    └── survey-snowball/
        ├── SKILL.md  README.md
        ├── config/{config.yaml, interest.md}
        └── scripts/{sources, build_graph, score_titles, make_cards, synthesize, snowball}.py
```

## 配合 Claude Code 使用

把 `skills/arxiv-scan/`、`skills/survey-snowball/` 放进你的 `~/.claude/skills/`(或项目 `.claude/skills/`),即可在 Claude Code 里用对应技能;启动壳仍可独立运行。

## 隐私 / 可分享

- 脚本全部 `__file__` 自定位、启动壳相对路径 —— **无任何绝对路径 / 用户名 / 邮箱硬编码**。
- 所有 API key 走环境变量 —— **key 不入库**。
- 个人内容只在 `interest.md`(已是空模板,填你自己的方向)。
- 运行产物(`.cache/`、`notes/`)已被 `.gitignore`,不入库。
