# survey-snowball —— 引文图驱动的专题调研工具

从种子论文沿引文图雪球式铺开 → 初筛 → 逐篇证据卡片 → 三轴综述(前人做了什么 / 可行性 / 突破口)。
数据源:**INSPIRE-HEP**(免 key)+ **NASA ADS**(需 token)。LLM 全走 DeepSeek。

## 目录

```
skills/survey-snowball/
  SKILL.md            技能说明(给 Claude 看)
  config/
    config.yaml       所有可调参数(深度/K/N/M/模型/通过档位…)
    interest.md       可选的长期研究画像(--use-interest 时才用)
  scripts/
    sources.py        INSPIRE + ADS 客户端(resolve / backward / forward)
    build_graph.py    引文图构造 + 去重 + 截断(确定性,无 LLM)
    score_titles.py   DeepSeek-flash 按 title 判四档初筛
    make_cards.py     抓 abstract + flash 出逐篇证据卡片
    synthesize.py     DeepSeek-V4-pro 三轴综述
    snowball.py       编排器(串起全流程 + 落盘)
  .cache/             中间产物(可清)
../../snowball.sh     启动壳(项目根)
```

## 快速开始

```bash
# key 写在 ~/.zshrc 时,在交互式终端里:
export DEEPSEEK_API_KEY=sk-...
export ADS_DEV_KEY=...        # 可选;无则只用 INSPIRE
./snowball.sh "2504.08514" --topic "用准万有关系约束中子星状态方程的可行性"
```

## 单源烟雾测试(免 DeepSeek,验证数据源通不通)

```bash
python3 skills/survey-snowball/scripts/sources.py 2504.08514
# 会打印 INSPIRE(及 ADS,若有 token)对该种子的 resolve / 参考文献数 / 施引数
```

## 可分享性(本工具不绑定任何个人信息)

- 脚本全部用 `__file__` 自定位,`snowball.sh` 用相对 `ROOT` —— **无任何 `/Users/...` 绝对路径、无用户名**。
- 所有 key 走环境变量(`DEEPSEEK_API_KEY` / `ADS_DEV_KEY`)—— **key 不写进任何文件**。
- 唯一的"个人内容"是可选的 `config/interest.md`(长期画像),默认空模板。

**分享前**:① 清空 `.cache/`(下载的论文摘要等中间产物);② `config/interest.md` 留空或换成对方的;
③ 让对方自备 `DEEPSEEK_API_KEY`、(可选)`ADS_DEV_KEY`。代码无需改动。

## 申请 ADS token

`ui.adsabs.harvard.edu` → 登录 → Account → API Token,复制后 `export ADS_DEV_KEY=...`(或写进 `~/.zshrc`)。
免费,日配额约 5000 次查询。无 token 也能跑,只是退化为 INSPIRE 单源。

## 注意

- 前向邻域(尤其施引者再取参考文献)调用量较大,一次完整调研可能要几分钟、几百次 API 调用;`config.yaml` 里的 K/expand_citers 控制规模。
- 卡片里的"支持/证伪"是 flash 的粗判线索,**不是定论**,最终以人工阅读为准。
- API 字段/语法若 INSPIRE/ADS 日后调整,改 `sources.py` 即可。
