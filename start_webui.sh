#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-}"
ENABLE_LOCAL_OUTPUT="${EDUPLUS_ENABLE_LOCAL_OUTPUT:-false}"
AUTO_DELETE_PUBLIC_DOWNLOADS="${EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS:-true}"
PUBLIC_JOB_TTL_SECONDS="${EDUPLUS_PUBLIC_JOB_TTL_SECONDS:-1800}"
CLEANUP_INTERVAL_SECONDS="${EDUPLUS_CLEANUP_INTERVAL_SECONDS:-60}"
PUBLIC_OUTPUT_ROOT="${EDUPLUS_PUBLIC_OUTPUT_ROOT:-downloads/web-jobs}"
BUNDLE_ROOT="${EDUPLUS_BUNDLE_ROOT:-downloads/web-bundles}"
LOCAL_OUTPUT_ROOT="${EDUPLUS_LOCAL_OUTPUT_ROOT:-downloads}"
USE_VENV=1

resolve_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$ROOT_DIR/$path"
  fi
}

print_help() {
  cat <<'EOF'
EDUPLUS 网页界面一键启动脚本

用法:
  bash start_webui.sh [选项]

也可以用环境变量:
  HOST=0.0.0.0 PORT=8000 bash start_webui.sh

参数:
  --host                        监听地址，默认 0.0.0.0
  --port                        监听端口，默认 8000
  --python                      指定 Python 可执行文件，默认自动查找 python3/python
  --no-venv                     不创建 .venv，直接使用当前 Python
  --enable-local-output         开启本地输出模式
  --disable-auto-delete-public-downloads
                                关闭公共模式下载后自动删除
  --public-job-ttl-seconds N    公共模式任务文件保留时长，默认 1800
  --cleanup-interval-seconds N  后台清理间隔，默认 60
  --public-output-root PATH     公共模式输出根目录，默认 downloads/web-jobs
  --bundle-root PATH            ZIP 打包目录，默认 downloads/web-bundles
  --local-output-root PATH      本地输出目录，默认 downloads
  -h, --help                    显示帮助
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
    --enable-local-output)
      ENABLE_LOCAL_OUTPUT=true
      shift
      ;;
    --disable-auto-delete-public-downloads)
      AUTO_DELETE_PUBLIC_DOWNLOADS=false
      shift
      ;;
    --public-job-ttl-seconds)
      PUBLIC_JOB_TTL_SECONDS="$2"
      shift 2
      ;;
    --cleanup-interval-seconds)
      CLEANUP_INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --public-output-root)
      PUBLIC_OUTPUT_ROOT="$2"
      shift 2
      ;;
    --bundle-root)
      BUNDLE_ROOT="$2"
      shift 2
      ;;
    --local-output-root)
      LOCAL_OUTPUT_ROOT="$2"
      shift 2
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

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Error: 找不到可用的 Python（已尝试 python3 和 python）" >&2
    exit 1
  fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: 找不到 Python: $PYTHON_BIN" >&2
  exit 1
fi

cd "$ROOT_DIR"
mkdir -p "$(resolve_path "$PUBLIC_OUTPUT_ROOT")"
mkdir -p "$(resolve_path "$BUNDLE_ROOT")"
mkdir -p "$(resolve_path "$LOCAL_OUTPUT_ROOT")"

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

echo "[4/4] 启动 EDUPLUS 网页界面"
echo
echo "访问地址:"
echo "  - 本机:   http://127.0.0.1:${PORT}"
if [[ -n "${LOCAL_IP}" ]]; then
  echo "  - 局域网: http://${LOCAL_IP}:${PORT}"
fi
echo
echo "按 Ctrl+C 停止服务"
echo "运行模式:"
echo "  - 公共模式输出: ${PUBLIC_OUTPUT_ROOT}"
echo "  - ZIP 打包目录: ${BUNDLE_ROOT}"
if [[ "$ENABLE_LOCAL_OUTPUT" == "true" ]]; then
  echo "  - 本地输出: 已开启（${LOCAL_OUTPUT_ROOT}）"
else
  echo "  - 本地输出: 未开启"
fi
echo

export EDUPLUS_ENABLE_LOCAL_OUTPUT="$ENABLE_LOCAL_OUTPUT"
export EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS="$AUTO_DELETE_PUBLIC_DOWNLOADS"
export EDUPLUS_PUBLIC_JOB_TTL_SECONDS="$PUBLIC_JOB_TTL_SECONDS"
export EDUPLUS_CLEANUP_INTERVAL_SECONDS="$CLEANUP_INTERVAL_SECONDS"
export EDUPLUS_PUBLIC_OUTPUT_ROOT="$PUBLIC_OUTPUT_ROOT"
export EDUPLUS_BUNDLE_ROOT="$BUNDLE_ROOT"
export EDUPLUS_LOCAL_OUTPUT_ROOT="$LOCAL_OUTPUT_ROOT"

SERVER_ARGS=(
  -m eduplus_tools.web
  --host "$HOST"
  --port "$PORT"
  --public-job-ttl-seconds "$PUBLIC_JOB_TTL_SECONDS"
  --cleanup-interval-seconds "$CLEANUP_INTERVAL_SECONDS"
  --public-output-root "$PUBLIC_OUTPUT_ROOT"
  --bundle-root "$BUNDLE_ROOT"
  --local-output-root "$LOCAL_OUTPUT_ROOT"
)

exec "$PYTHON_BIN" "${SERVER_ARGS[@]}"
