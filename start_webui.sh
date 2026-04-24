#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USE_VENV=1

print_help() {
  cat <<'EOF'
EDUPLUS Web UI 一键启动脚本

用法:
  bash start_webui.sh [--host 0.0.0.0] [--port 8000] [--python python3] [--no-venv]

也可以用环境变量:
  HOST=0.0.0.0 PORT=8000 bash start_webui.sh

参数:
  --host      监听地址，默认 0.0.0.0
  --port      监听端口，默认 8000
  --python    指定 Python 可执行文件，默认 python3
  --no-venv   不创建 .venv，直接使用当前 Python
  -h, --help  显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-venv)
      USE_VENV=0
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo >&2
      print_help >&2
      exit 1
      ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: 找不到 Python: $PYTHON_BIN" >&2
  exit 1
fi

cd "$ROOT_DIR"
mkdir -p downloads

if [[ "$USE_VENV" -eq 1 ]]; then
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[1/4] 创建虚拟环境: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    echo "[1/4] 虚拟环境已存在: $VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON_BIN="python"
else
  echo "[1/4] 跳过虚拟环境，使用当前 Python"
fi

echo "[2/4] 升级 pip"
"$PYTHON_BIN" -m pip install --upgrade pip

echo "[3/4] 安装项目依赖"
"$PYTHON_BIN" -m pip install -r requirements.txt

LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo "[4/4] 启动 EDUPLUS Web UI"
echo
echo "访问地址:"
echo "  - 本机:   http://127.0.0.1:${PORT}"
if [[ -n "${LOCAL_IP}" ]]; then
  echo "  - 局域网: http://${LOCAL_IP}:${PORT}"
fi
echo
echo "按 Ctrl+C 停止服务"
echo

exec "$PYTHON_BIN" -m eduplus_tools.web --host "$HOST" --port "$PORT"
