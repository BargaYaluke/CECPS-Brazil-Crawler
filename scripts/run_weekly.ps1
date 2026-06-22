# run_weekly.ps1 -- lite pipeline: crawl -> enrich/translate -> report.xlsx -> equipment bids table.
#
# Crawl is by PUBLICATION date.
# TWO modes:
#   First crawl (empty DB):   -First   -> fetch every tender PUBLISHED in the last N days.
#   Weekly incremental:       (no flag) -> purge expired, then incremental new/changed (atualizacao).
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 misreads non-ASCII in a
# BOM-less UTF-8 .ps1, so all script text is English; the Chinese progress lines come
# from the Python CLI itself (it sets PYTHONIOENCODING=utf-8).
#
# Usage (from anywhere; it cd's to the repo root itself):
#   FIRST TIME (DB is empty -> populate with the last month's published tenders):
#     powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -First
#   EVERY WEEK after that (incremental update):
#     powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
#   Options:
#     -AllMode          also emit the "all (incl. expired)" equipment table
#
# Schedule the weekly run via Task Scheduler (example, Monday 07:00; replace <repo>):
#   schtasks /Create /TN "brazil-weekly" /SC WEEKLY /D MON /ST 07:00 ^
#     /TR "powershell -ExecutionPolicy Bypass -File <repo>\scripts\run_weekly.ps1"

param(
    [switch]$First,    # first crawl: pull tenders PUBLISHED in the last month
    [switch]$AllMode   # also run find_bids.py in "all" mode (includes expired)
)
# Crawl is ALWAYS by publication date (fixed): first run = last 1 month, weekly = last 1 week.

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
Set-Location (Split-Path $PSScriptRoot -Parent)   # -> repo root
$py = "C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe"

$today = (Get-Date).ToString("yyyy-MM-dd")
$startDate = (Get-Date).AddDays(-30).ToString("yyyy-MM-dd")            # first crawl: last 1 month of publications (fixed)
$weekStart = (Get-Date).AddDays(-7).ToString("yyyy-MM-dd")            # weekly: last 1 week of publications (fixed)
$stamp = (Get-Date).ToString("yyyy-MM-dd")                            # shared timestamp for output filenames
$env:RUN_STAMP = $stamp                                               # find_bids.py reads this for its output filename
$results = @()
# Note: translate / export-excel filter to un-expired + 20w-200w CNY BY DEFAULT (CLI defaults),
# so no extra flags are needed here. Use --no-active-only / --cny-min 0 to widen if ever needed.

# ---- logging: full run transcript (logs/run_<ts>.log) + per-stage metrics CSV ----
# The per-stage metrics CSV (logs/pipeline_metrics.csv, columns 时间/阶段/状态/关键指标/耗时)
# is written by the Python side (src.core.logger.log_stage) after each stage.
$logDir = Join-Path (Get-Location) "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runLog = Join-Path $logDir ("run_" + (Get-Date).ToString("yyyy-MM-dd_HHmmss") + ".log")
$metricsCsv = Join-Path $logDir "pipeline_metrics.csv"
try { Start-Transcript -Path $runLog | Out-Null } catch {}

# Run a labelled step. $argv is the full argument list passed to python (after $py).
function Run([string]$name, [string[]]$argv) {
    Write-Host ""
    Write-Host ("===== $name =====") -ForegroundColor Cyan
    Write-Host ("> python " + ($argv -join " ")) -ForegroundColor DarkGray
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $py @argv
    $code = $LASTEXITCODE
    $sw.Stop()
    $ok = ($code -eq 0)
    $tag = if ($ok) { "OK" } else { "FAIL($code)" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}  ({2:N0}s)" -f $tag, $name, $sw.Elapsed.TotalSeconds) -ForegroundColor $color
    $script:results += [pscustomobject]@{ Step = $name; Status = $tag; Seconds = [int]$sw.Elapsed.TotalSeconds }
}

$mode = if ($First) { "FIRST crawl" } else { "WEEKLY incremental" }
Write-Host ("=== Lite pipeline [{0}] | {1} | open-bid horizon -> {2} ===" -f $mode, $today, $dataFinal) -ForegroundColor Yellow

# ---- crawl (by PUBLICATION date) ----
if ($First) {
    # First run on an empty DB: pull every tender PUBLISHED in [$startDate, $today]
    # (by publication date, regardless of deadline) plus their line items.
    Run "1-fetch-and-store" @("-m", "src.cli", "fetch-and-store", "--start", $startDate, "--end", $today)
} else {
    # Weekly: (1) purge expired (logs deleted ones to logs/expired_purged.csv),
    #         (2) check existing tenders for updates (incremental via sync_cursor),
    #         (3) crawl tenders PUBLISHED in the last 7 days.
    Run "0-purge-expired" @("-m", "src.cli", "purge-expired")
    Run "1-atualizacao" @("-m", "src.cli", "fetch-atualizacao")
    Run "2-fetch-and-store" @("-m", "src.cli", "fetch-and-store", "--start", $weekStart, "--end", $today)
}

# ---- enrich -> translate (filtered) -> report (filtered) -> equipment table ----
Run "3-enrich" @("-m", "src.cli", "enrich")
# translate + export default to the working set (un-expired AND 20w-200w CNY).
Run "4-translate" @("-m", "src.cli", "translate")
Run "5-export" @("-m", "src.cli", "export-excel", "--out", "data/exports/report_$stamp.xlsx")
# equipment table: the existing 实物设备/产品 logic (reads RUN_STAMP for its timestamped filename).
Run "6-equipment-bids" @("analysis/equipment_bids/find_bids.py")
if ($AllMode) {
    Run "6b-equipment-bids-all" @("analysis/equipment_bids/find_bids.py", "all")
}

Write-Host "`n===== Summary =====" -ForegroundColor Yellow
$results | Format-Table -AutoSize

$snapshot = Join-Path (Get-Location) "data\exports\report_$stamp.xlsx"
if (Test-Path $snapshot) { Write-Host ("Data snapshot: " + $snapshot) -ForegroundColor Green }
# match the equipment table by its ASCII-safe pattern (filename itself is Chinese)
$equip = Get-ChildItem -Path "data\exports" -Filter "*20-200*CNY*.xlsx" -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -First 1
if ($equip) { Write-Host ("Equipment bids: " + $equip.FullName) -ForegroundColor Green }
Write-Host ("Run log:        " + $runLog) -ForegroundColor Green
Write-Host ("Stage metrics:  " + $metricsCsv) -ForegroundColor Green
$purgeLog = Join-Path $logDir "expired_purged.csv"
if (Test-Path $purgeLog) { Write-Host ("Expired purged: " + $purgeLog) -ForegroundColor Green }

try { Stop-Transcript | Out-Null } catch {}

# non-zero exit if any step failed (so Task Scheduler can flag it).
if ($results | Where-Object { $_.Status -ne "OK" }) { exit 1 } else { exit 0 }
