# 서버/워커를 죽어도 다시 띄우는 감시 루프. 창을 닫기 전까지 계속 돈다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\keep_alive.ps1 -Role worker
#   powershell -ExecutionPolicy Bypass -File scripts\keep_alive.ps1 -Role server -Python <venv python>
#
# 워커는 GoodNotes 가 꺼져 있으면 시작 직후 종료된다. 그래도 10초마다 다시
# 시도하므로, GoodNotes 를 켜 두기만 하면 잡이 들어오는 즉시 그린다.
param(
    [ValidateSet("worker", "server")][string]$Role = "worker",
    [string]$Python = "python",
    [string]$Server = "http://127.0.0.1:8000",
    [string]$Token  = "local-demo-token",
    [int]$RetrySec  = 10
)
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"; $env:MPLBACKEND = "Agg"
if ($Role -eq "server") {
    $env:DATABASE_URL = "sqlite:///./h2i.db"; $env:WORKER_TOKEN = $Token
    $dir = Join-Path $repo "server"; $log = Join-Path $dir "e2e.log"
    $cmd = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000")
} else {
    $dir = Join-Path $repo "windows_worker"; $log = Join-Path $dir "work\worker.log"
    New-Item -ItemType Directory -Force (Join-Path $dir "work") | Out-Null
    $cmd = @("worker.py", "--server", $Server, "--token", $Token, "--execute", "--poll_interval", "3")
}
Set-Location $dir
while ($true) {
    Add-Content $log "[$(Get-Date -Format HH:mm:ss)] keep_alive: $Role 시작"
    & $Python @cmd *>> $log
    Add-Content $log "[$(Get-Date -Format HH:mm:ss)] keep_alive: $Role 종료(코드 $LASTEXITCODE) - ${RetrySec}초 후 재시작"
    Start-Sleep -Seconds $RetrySec
}
