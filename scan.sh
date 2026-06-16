#!/usr/bin/env bash
# scan.sh —— 刷 arxiv 初筛日报(arxiv-scan 的启动壳)
#   ./scan.sh              扫描【今天】
#   ./scan.sh 2026-06-11   扫描【指定日期】(YYYY-MM-DD)
# 当日无数据会直接提示「当日无数据」,不产出空日报。
# 需要环境变量 DEEPSEEK_API_KEY(打分/总结用);若写在 ~/.zshrc,请在终端里直接运行本脚本。

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
DATE="${1:-today}"

if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "⚠️  未检测到 DEEPSEEK_API_KEY(打分/总结需要)。"
  echo "   若已写入 ~/.zshrc,请在交互式终端里直接运行本脚本;否则先: export DEEPSEEK_API_KEY=sk-..."
  echo "   (仅查"当日有无数据"不需要 key,会继续;真要打分会在该步停下。)"
fi

python3 skills/arxiv-scan/scripts/arxiv_scan.py --date "$DATE"
