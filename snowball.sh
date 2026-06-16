#!/usr/bin/env bash
# snowball.sh —— 引文图驱动的专题调研(survey-snowball 的启动壳)
#   ./snowball.sh "2504.08514" --topic "用准万有关系约束中子星状态方程的可行性"
#   ./snowball.sh "2504.08514" "10.1103/PhysRevD.xxx" --topic "…" --use-interest
#   ./snowball.sh "<arxiv/doi/recid/bibcode/标题>" [更多种子…] [--topic "…"] [--depth 2] [--no-forward] [--reuse]
#
# 种子可给多篇(arxiv id / DOI / INSPIRE recid / ADS bibcode / 直接标题),图取并集。
# 相关性标尺三选其一或组合:--topic(本次思路) / --use-interest(长期画像) / 种子主题(默认就用,--no-seed 关)。
#
# 需要环境变量:DEEPSEEK_API_KEY(初筛/卡片/综述);ADS_DEV_KEY(可选,缺则只用 INSPIRE)。
# 若 key 写在 ~/.zshrc,请在交互式终端里直接运行本脚本(或用 zsh -ic 包一层)。

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

# 把开头连续的【非 -- 开头】参数当作种子,其余原样透传给 python
SEEDS=()
while [ $# -gt 0 ] && [[ "$1" != --* ]]; do
  SEEDS+=("$1"); shift
done
if [ ${#SEEDS[@]} -eq 0 ]; then
  echo "用法: ./snowball.sh \"<种子: arxiv/doi/recid/bibcode/标题>\" [更多种子…] [--topic \"本次思路\"] [--use-interest] [--depth 2] [--no-forward] [--reuse]"
  exit 1
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "⚠️  未检测到 DEEPSEEK_API_KEY(初筛/卡片/综述需要)。"
  echo "   若已写入 ~/.zshrc,请在交互式终端里直接运行;否则先: export DEEPSEEK_API_KEY=sk-..."
fi
if [ -z "$ADS_DEV_KEY" ]; then
  echo "ℹ️  未检测到 ADS_DEV_KEY → 只用 INSPIRE 单源(astro 覆盖会变弱)。申请: ui.adsabs.harvard.edu → Account → API Token"
fi

python3 skills/survey-snowball/scripts/snowball.py --seeds "${SEEDS[@]}" "$@"
