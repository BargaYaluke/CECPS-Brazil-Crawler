# run_weekly.ps1 -- lite weekly one-click pipeline:
#   crawl -> enrich/translate -> report.xlsx -> equipment/product bids table.
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 misreads non-ASCII in a
# BOM-less UTF-8 .ps1, so all script text is English; the Chinese progress lines come
# from the Python CLI itself (it sets PYTHONIOENCODING=utf-8).
#
# Usage (from anywhere; it cd's to the repo root itself):
#   powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
#       -> regular weekly run: incremental crawl (sync_cursor) + open-bid snapshot.
#   powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -BackfillDays 60
#       -> first run / periodic backfill: also fetch-and-store the last 60 publication days.
#   powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -AllMode
#       -> additionally emit the "all (incl. expired)" equipment table.
#
# Schedule weekly via Task Scheduler (example, Monday 07:00; replace <repo> with the
# absolute repo path):
#   schtasks /Create /TN "brazil-weekly" /SC WEEKLY /D MON /ST 07:00 ^
#     /TR "powershell -ExecutionPolicy Bypass -File <repo>\scripts\run_weekly.ps1"

param(
    [int]$BackfillDays = 0,   # >0: fetch-and-store the last N publication days first (first run / backfill)
    [switch]$AllMode          # also run find_bids.py in "all" mode (includes expired)
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
Set-Location (Split-Path $PSScriptRoot -Parent)   # -> repo root
$py = "C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe"

$today = (Get-Date).ToString("yyyy-MM-dd")
$results = @()

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

Write-Host ("=== Lite weekly pipeline | $today ===") -ForegroundColor Yellow

# 0) optional backfill: fetch-and-store the last N publication days (first run / periodic).
if ($BackfillDays -gt 0) {
    $start = (Get-Date).AddDays(-$BackfillDays).ToString("yyyy-MM-dd")
    Run "0-backfill" @("-m", "src.cli", "fetch-and-store", "--start", $start, "--end", $today)
}

# 1) incremental crawl: PNCP /contratacoes/atualizacao via sync_cursor (first run falls back to today-7d).
Run "1-atualizacao" @("-m", "src.cli", "fetch-atualizacao")

# 2) open-bid snapshot: tenders still open for proposals (keeps the un-expired pool fresh).
Run "2-proposta" @("-m", "src.cli", "fetch-proposta")

# 3) enrich: region + BRL->CNY (valor_cny_estimado, required by the equipment table's CNY band).
Run "3-enrich" @("-m", "src.cli", "enrich")

# 4) translate: PT->CN tender summaries (DeepSeek, cached by source text).
Run "4-translate" @("-m", "src.cli", "translate")

# 5) export Excel data snapshot (report.xlsx).
Run "5-export" @("-m", "src.cli", "export-excel", "--out", "data/exports/report.xlsx")

# 6) generate the equipment/product bids table (active = un-expired).
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

# non-zero exit if any step failed (so Task Scheduler can flag it).
if ($results | Where-Object { $_.Status -ne "OK" }) { exit 1 } else { exit 0 }
