# run_report.ps1 -- business report pipeline (steps 2..8) with per-step progress + timing.
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 misreads non-ASCII in a
# BOM-less UTF-8 .ps1, so all script text is English; the Chinese progress lines come
# from the Python CLI itself (it sets PYTHONIOENCODING=utf-8).
#
# Usage (from anywhere; it cd's to the repo root itself):
#   powershell -ExecutionPolicy Bypass -File scripts\run_report.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_report.ps1 -Start 2026-05-22 -End 2026-05-28
#   powershell -ExecutionPolicy Bypass -File scripts\run_report.ps1 -SkipCatalog   # if catalog already loaded
#   powershell -ExecutionPolicy Bypass -File scripts\run_report.ps1 -PcaLimit 5000

param(
    [string]$Start = "2026-05-22",
    [string]$End   = "2026-05-28"
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
Set-Location (Split-Path $PSScriptRoot -Parent)   # -> repo root
$py = "C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe"

$results = @()

function Step([string]$name, [string[]]$cmdArgs) {
    Write-Host ""
    Write-Host ("===== $name =====") -ForegroundColor Cyan
    Write-Host ("> python -m src.cli " + ($cmdArgs -join " ")) -ForegroundColor DarkGray
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $py -m src.cli @cmdArgs
    $code = $LASTEXITCODE
    $sw.Stop()
    $ok = ($code -eq 0)
    $tag = if ($ok) { "OK" } else { "FAIL($code)" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}  ({2:N0}s)" -f $tag, $name, $sw.Elapsed.TotalSeconds) -ForegroundColor $color
    $script:results += [pscustomobject]@{ Step = $name; Status = $tag; Seconds = [int]$sw.Elapsed.TotalSeconds }
}

Write-Host ("=== Lite crawler pipeline | dates {0}..{1} ===" -f $Start, $End) -ForegroundColor Yellow

# 1) fetch-and-store: publicacao tenders + line items -> contratacoes / itens
Step "1-fetch-and-store" @("fetch-and-store", "--start", $Start, "--end", $End)

# 2) fetch-proposta: snapshot of currently-open bids (deadline >= today)
Step "2-fetch-proposta" @("fetch-proposta")

# 3) enrich (region / fx -> valor_cny_estimado, required by the equipment table)
Step "3-enrich" @("enrich")

# 4) translate (PT->CN tender summaries, cached by source text)
Step "4-translate" @("translate")

# 5) export Excel data snapshot (report.xlsx)
Step "5-export" @("export-excel", "--out", "data/exports/report.xlsx")

Write-Host "`n===== Summary =====" -ForegroundColor Yellow
$results | Format-Table -AutoSize
$xlsx = Join-Path (Get-Location) "data\exports\report.xlsx"
if (Test-Path $xlsx) { Write-Host ("Report: " + $xlsx) -ForegroundColor Green }
