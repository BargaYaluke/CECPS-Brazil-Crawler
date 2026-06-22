# run_weekly.ps1 -- lite pipeline: crawl -> enrich/translate -> report.xlsx -> equipment bids table.
#
# TWO modes:
#   First crawl (empty DB):   -First   -> fetch the current OPEN-bid universe (proposta).
#   Weekly incremental:       (no flag) -> incremental new/changed (atualizacao) + refresh open pool.
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 misreads non-ASCII in a
# BOM-less UTF-8 .ps1, so all script text is English; the Chinese progress lines come
# from the Python CLI itself (it sets PYTHONIOENCODING=utf-8).
#
# Usage (from anywhere; it cd's to the repo root itself):
#   FIRST TIME (DB is empty -> populate with currently-biddable tenders):
#     powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -First
#   EVERY WEEK after that (incremental update):
#     powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
#   Options:
#     -AllMode             also emit the "all (incl. expired)" equipment table
#     -OpenHorizonDays N   open-bid snapshot reaches N days into the future (default 30)
#
# Schedule the weekly run via Task Scheduler (example, Monday 07:00; replace <repo>):
#   schtasks /Create /TN "brazil-weekly" /SC WEEKLY /D MON /ST 07:00 ^
#     /TR "powershell -ExecutionPolicy Bypass -File <repo>\scripts\run_weekly.ps1"

param(
    [switch]$First,              # first crawl: grab the current open-bid universe into an empty DB
    [switch]$AllMode,            # also run find_bids.py in "all" mode (includes expired)
    [int]$OpenHorizonDays = 30   # open-bid snapshot: tenders with a deadline up to N days ahead
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
Set-Location (Split-Path $PSScriptRoot -Parent)   # -> repo root
$py = "C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe"

$today = (Get-Date).ToString("yyyy-MM-dd")
$dataFinal = (Get-Date).AddDays($OpenHorizonDays).ToString("yyyy-MM-dd")
$results = @()

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

# ---- crawl ----
if ($First) {
    # First run on an empty DB: pull the current OPEN-bid universe (tenders still receiving proposals,
    # with deadline up to $dataFinal) plus their line items. No historical-publication backfill.
    Run "1-proposta-init" @("-m", "src.cli", "fetch-proposta", "--data-final", $dataFinal)
} else {
    # Weekly: incremental new/changed tenders via sync_cursor, then refresh the open-bid snapshot.
    Run "1-atualizacao" @("-m", "src.cli", "fetch-atualizacao")
    Run "2-proposta-refresh" @("-m", "src.cli", "fetch-proposta", "--data-final", $dataFinal)
}

# ---- enrich / translate / export / equipment table (same for both modes) ----
Run "3-enrich" @("-m", "src.cli", "enrich")
Run "4-translate" @("-m", "src.cli", "translate")
Run "5-export" @("-m", "src.cli", "export-excel", "--out", "data/exports/report.xlsx")
Run "6-equipment-bids" @("analysis/equipment_bids/find_bids.py")
if ($AllMode) {
    Run "6b-equipment-bids-all" @("analysis/equipment_bids/find_bids.py", "all")
}

Write-Host "`n===== Summary =====" -ForegroundColor Yellow
$results | Format-Table -AutoSize

$snapshot = Join-Path (Get-Location) "data\exports\report.xlsx"
if (Test-Path $snapshot) { Write-Host ("Data snapshot: " + $snapshot) -ForegroundColor Green }
# match the equipment table by its ASCII-safe pattern (filename itself is Chinese)
$equip = Get-ChildItem -Path "data\exports" -Filter "*20-200*CNY*.xlsx" -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -First 1
if ($equip) { Write-Host ("Equipment bids: " + $equip.FullName) -ForegroundColor Green }
Write-Host ("Run log:        " + $runLog) -ForegroundColor Green
Write-Host ("Stage metrics:  " + $metricsCsv) -ForegroundColor Green

try { Stop-Transcript | Out-Null } catch {}

# non-zero exit if any step failed (so Task Scheduler can flag it).
if ($results | Where-Object { $_.Status -ne "OK" }) { exit 1 } else { exit 0 }
