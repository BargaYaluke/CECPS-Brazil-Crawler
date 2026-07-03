#!/usr/bin/env bash
# run_weekly.sh -- Linux 版周更流水线 (run_weekly.ps1 的移植)
# crawl -> enrich/translate -> report.xlsx -> equipment bids table
#
# 两种模式:
#   首次(空库):  ./scripts/run_weekly.sh --first   抓过去 30 天发布的标
#   每周(增量):  ./scripts/run_weekly.sh           清过期 -> 查更新 -> 抓上周
# 选项:
#   --all   额外出"全量含过期"设备表
#
# cron 示例(每周一 07:00, 把 <repo> 换成实际路径):
#   0 7 * * 1 cd <repo> && ./scripts/run_weekly.sh >> logs/cron.log 2>&1
set -uo pipefail

# 切到 repo 根目录(脚本所在目录的上一级)
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"

# 解析 Python 解释器,优先级:
#   1) 环境变量 BRAZIL_PY 显式指定
#   2) repo 根下的 .venv
#   3) Miniconda 的 brazil_crawler 环境
#   4) 系统 python3(注意:服务器系统自带可能是 3.6,太老)
PY="${BRAZIL_PY:-}"
[ -n "$PY" ] && [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$HOME/miniconda3/envs/brazil_crawler/bin/python"
[ -x "$PY" ] || PY="python3"

export PYTHONIOENCODING=utf-8
export LANG=${LANG:-C.UTF-8}

FIRST=0
ALLMODE=0
FX_RATE="${FX_RATE:-}"          # 也可用环境变量 FX_RATE 预置
while [ $# -gt 0 ]; do
  case "$1" in
    --first)      FIRST=1 ;;
    --all)        ALLMODE=1 ;;
    --fx-rate)    shift; FX_RATE="$1" ;;     # --fx-rate 1.30
    --fx-rate=*)  FX_RATE="${1#*=}" ;;       # --fx-rate=1.30
    *) echo "unknown arg: $1" >&2 ;;
  esac
  shift
done

# 没给汇率、且是交互式终端 → 当场询问;非交互(cron / nohup 重定向了 stdin)则留空,
# enrich 会自动回退在线汇率 API。
if [ -z "$FX_RATE" ] && [ -t 0 ]; then
  printf "当前汇率 1 BRL = ? CNY  (直接回车 = 用在线API自动获取): " > /dev/tty
  read FX_RATE < /dev/tty
fi

# 校验:给了就必须是数字(如 1.30),否则直接退出,避免拿垃圾值算钱。
if [ -n "$FX_RATE" ]; then
  case "$FX_RATE" in
    ''|*[!0-9.]*) echo "✗ 汇率格式不对: '$FX_RATE' (应为数字,如 1.30)" >&2; exit 1 ;;
  esac
  echo "→ 使用手动汇率: 1 BRL = $FX_RATE CNY (跳过在线汇率API)"
else
  echo "→ 未提供汇率,enrich 将走在线汇率API"
fi

TODAY=$(date +%F)
START=$(date -d "-30 days" +%F)    # 首次: 过去 30 天发布
WEEK=$(date -d "-7 days" +%F)      # 周更: 过去 7 天发布
export RUN_STAMP="$TODAY"          # find_bids.py 读它做输出文件名

mkdir -p logs data/exports
RUNLOG="logs/run_$(date +%F_%H%M%S).log"

declare -a SUMMARY
run() {  # run "名字" python-args...
  local name="$1"; shift
  echo "" | tee -a "$RUNLOG"
  echo "===== $name =====" | tee -a "$RUNLOG"
  echo "> python $*" | tee -a "$RUNLOG"
  local t0=$SECONDS
  if "$PY" "$@" 2>&1 | tee -a "$RUNLOG"; then
    local rc=${PIPESTATUS[0]}
  else
    local rc=${PIPESTATUS[0]}
  fi
  local dt=$((SECONDS - t0))
  if [ "$rc" -eq 0 ]; then
    echo "[OK] $name (${dt}s)" | tee -a "$RUNLOG"
    SUMMARY+=("OK    $name ${dt}s")
  else
    echo "[FAIL($rc)] $name (${dt}s)" | tee -a "$RUNLOG"
    SUMMARY+=("FAIL  $name ${dt}s")
  fi
}

if [ "$FIRST" -eq 1 ]; then
  echo "=== Lite pipeline [FIRST crawl] | $TODAY ==="
  run "1-fetch-and-store" -m src.cli fetch-and-store --start "$START" --end "$TODAY"
else
  echo "=== Lite pipeline [WEEKLY incremental] | $TODAY ==="
  run "0-purge-expired"   -m src.cli purge-expired
  run "1-atualizacao"     -m src.cli fetch-atualizacao
  run "2-fetch-and-store" -m src.cli fetch-and-store --start "$WEEK" --end "$TODAY"
fi

# enrich:有手动汇率就带上 --fx-rate,没有则在线取
if [ -n "$FX_RATE" ]; then
  run "3-enrich"        -m src.cli enrich --fx-rate "$FX_RATE"
else
  run "3-enrich"        -m src.cli enrich
fi
run "4-translate"       -m src.cli translate
run "5-export"          -m src.cli export-excel --out "data/exports/report_${RUN_STAMP}.xlsx"
run "6-equipment-bids"  analysis/equipment_bids/find_bids.py
[ "$ALLMODE" -eq 1 ] && run "6b-equipment-bids-all" analysis/equipment_bids/find_bids.py all

echo "" | tee -a "$RUNLOG"
echo "===== Summary =====" | tee -a "$RUNLOG"
printf '%s\n' "${SUMMARY[@]}" | tee -a "$RUNLOG"
echo "Run log: $RUNLOG"

# 任一步失败则非 0 退出(cron 可据此告警)
for s in "${SUMMARY[@]}"; do [[ "$s" == FAIL* ]] && exit 1; done
exit 0
