# Windows 워커를 상시 모드로 띄운다. 서버에 잡이 올라오면 가져가서
# GoodNotes 에 필기한다. Ctrl+C 로 종료.
#
# 사용: powershell -ExecutionPolicy Bypass -File scripts\run_worker.ps1
#       powershell -ExecutionPolicy Bypass -File scripts\run_worker.ps1 -Server https://<railway>
#
# 실행 전 확인:
#   1) GoodNotes for Windows 에서 '소페_시연용' 노트북의 '빈 페이지' 를 열어둘 것
#      (워커는 페이지를 넘기지 않는다. 잉크가 있으면 경고하고 덧그린다)
#   2) 처음이라면 한 번만:  python worker.py --calibrate

param(
    [string]$Server = "http://127.0.0.1:8000",
    [string]$Token  = $env:H2I_WORKER_TOKEN,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$worker = Join-Path $repo "windows_worker"

if (-not $Token) { $Token = "local-demo-token" }

$venvPy = Join-Path $repo ".venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "서버 : $Server"
Write-Host "모드 : $(if ($DryRun) { 'dry-run (마우스 안 잡음)' } else { 'EXECUTE (마우스 점유)' })"
Write-Host "중단 : Ctrl+C, 또는 마우스를 화면 좌상단 모서리로"
Write-Host ""

Push-Location $worker
try {
    $args = @("worker.py", "--server", $Server, "--token", $Token, "--poll_interval", "3")
    if (-not $DryRun) { $args += "--execute" }
    & $py @args
} finally {
    Pop-Location
}
