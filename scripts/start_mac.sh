#!/usr/bin/env bash
# pdf2zh 一键启动脚本 (macOS)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
    echo "[!] 未找到 .venv 虚拟环境，请先初始化 Python 虚拟环境"
    exit 1
fi

echo "[*] 激活虚拟环境..."
source .venv/bin/activate

export PDF2ZH_PORT="${PDF2ZH_PORT:-7862}"

echo "[*] 正在启动 pdf2zh Web UI (端口: $PDF2ZH_PORT)..."
echo "[*] 访问地址: http://127.0.0.1:$PDF2ZH_PORT"

python -m pdf2zh.webui
