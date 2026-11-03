#!/usr/bin/env bash
# 一键启动：自动补数据/构建 → 选运行时 → 端口就绪后自动打开浏览器
# 逻辑在 scripts/launch.py 里（跨平台与 start.bat 完全一致）
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

if ! command -v python >/dev/null 2>&1; then
  echo "[错误] 未找到 python，请先安装 Python 3.10+"
  exit 1
fi

exec python scripts/launch.py --port "${PORT}"
