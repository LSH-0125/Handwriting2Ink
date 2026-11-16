# 노트북에서 로컬 서버를 띄운다. 아이패드는 같은 Wi-Fi 에서
#   http://<이 PC 의 LAN IP>:8000/
# 을 열면 된다. Railway 가 막혔을 때의 폴백 경로이기도 하다.
#
# 사용: powershell -ExecutionPolicy Bypass -File scripts\run_local_server.ps1

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo ".venv"
$py   = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[1/3] .venv 생성"
    python -m venv $venv
    Write-Host "[2/3] 의존성 설치 (수 분 소요)"
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r (Join-Path $repo "server\requirements.txt")
} else {
    Write-Host "[1-2/3] .venv 재사용: $venv"
}

$envFile = Join-Path $repo "server\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "server\.env 가 없습니다. .env.example 을 복사해서 만듭니다."
    Copy-Item (Join-Path $repo "server\.env.example") $envFile
    # 로컬 LAN 시연에서는 접근키 없이 쓰는 편이 편하다.
    (Get-Content $envFile) -replace '^WORKER_TOKEN=.*', 'WORKER_TOKEN=local-demo-token' |
        Set-Content $envFile -Encoding ascii
}

# 아이패드가 접속할 주소를 알려준다
Write-Host ""
Write-Host "아이패드에서 열 주소:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    ForEach-Object { Write-Host ("  http://{0}:8000/   ({1})" -f $_.IPAddress, $_.InterfaceAlias) }
Write-Host ""
Write-Host "[3/3] 서버 기동 (Ctrl+C 로 종료)"

Push-Location (Join-Path $repo "server")
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
