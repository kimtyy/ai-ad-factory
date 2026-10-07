<#
scripts/watchdog.ps1 - 대시보드(Streamlit 8501) + 모바일 터널(cloudflared) 감시·자동 복구

- 10초마다 점검. 프로세스가 죽었으면(크래시·강제 종료) 다음 점검에서 바로 재시작 (최대 약 10초)
- 프로세스는 살아 있는데 /_stcore/health가 6번 연속(약 60초) 응답 없으면 멈춘 것으로 보고 재시작
  (렌더 중에도 health는 응답하므로 렌더를 크래시로 오인하지 않음)
- 터널: cloudflared 프로세스가 없으면 바로, 공개 주소 health가 3번 연속(30초 간격) 실패하면 재시작
  (임시 터널은 Cloudflare가 몇 시간 뒤 삭제함: "Tunnel not found") → 새 주소를 outputs\tunnel_url.txt에 기록
- 서버·터널은 독립 콘솔(cmd /c)로 띄움: 감시 창·도구 창이 닫혀도 같이 죽지 않게
- 중복 실행 방지: 이미 감시 중이면 바로 종료 (Global 뮤텍스)
- 로그: outputs\watchdog.log

수동 실행: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog.ps1
자동 실행: scripts\install_watchdog.ps1 (Windows 로그인 시 작업 스케줄러로 시작)
#>
param(
    [int]$IntervalSec = 10,
    [int]$FailLimit = 6,          # 대시보드 health 연속 실패 허용 (x IntervalSec = 약 60초)
    [int]$TunnelEvery = 3,        # 터널 공개 주소 점검 주기 (틱 수, 약 30초: 외부 왕복이라 덜 자주)
    [int]$TunnelFailLimit = 3,
    [switch]$Once          # 테스트용: 한 번만 점검하고 종료
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "outputs"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Log = Join-Path $Out "watchdog.log"
$UrlFile = Join-Path $Out "tunnel_url.txt"
$TunnelLog = Join-Path $env:TEMP "cloudflared_8501.log"
$HealthUrl = "http://localhost:8501/_stcore/health"

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Test-Health([string]$url, [int]$timeout = 10) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout
        return ($r.StatusCode -eq 200 -and $r.Content -match "ok")
    } catch { return $false }
}

function Get-StreamlitProcs {
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "streamlit" -and $_.CommandLine -match "8501" -and $_.Name -ne "powershell.exe" }
}

function Start-Dashboard {
    Get-StreamlitProcs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    $cmd = Join-Path $PSScriptRoot "run_dashboard.cmd"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$cmd`"" -WindowStyle Hidden
    Write-Log "dashboard: (re)started"
}

function Start-Tunnel {
    Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $cmd = Join-Path $PSScriptRoot "run_tunnel.cmd"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$cmd`"" -WindowStyle Hidden
    $url = $null
    for ($i = 0; $i -lt 40 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $TunnelLog) {
            $m = Select-String -Path $TunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value }
        }
    }
    if ($url) {
        Set-Content -Path $UrlFile -Value $url -Encoding utf8
        Write-Log "tunnel: (re)started -> $url"
    } else {
        Write-Log "tunnel: started but no URL found yet"
    }
    return $url
}

function Get-TunnelUrl {
    if (Test-Path $UrlFile) { return (Get-Content $UrlFile -Raw).Trim() }
    # 감시를 처음 켤 때 이미 떠 있는 터널이 있으면 로그의 주소를 이어받음 (멀쩡한 터널을 재시작해 주소가 바뀌지 않게)
    if ((Get-Process -Name cloudflared -ErrorAction SilentlyContinue) -and (Test-Path $TunnelLog)) {
        $m = Select-String -Path $TunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m -and (Test-Health "$($m.Matches[0].Value)/_stcore/health" 20)) {
            Set-Content -Path $UrlFile -Value $m.Matches[0].Value -Encoding utf8
            Write-Log "tunnel: adopted running tunnel -> $($m.Matches[0].Value)"
            return $m.Matches[0].Value
        }
    }
    return $null
}

# ---------------------------------------------------------------- 중복 실행 방지
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\AdFactoryWatchdog", [ref]$created)
if (-not $created) {
    Write-Log "watchdog: already running, exit"
    exit 0
}

Write-Log "watchdog: start (interval ${IntervalSec}s, dashboard hang limit $FailLimit, tunnel check every $TunnelEvery ticks)"
$dashFails = 0
$tunnelFails = 0
$tick = 0
try {
    while ($true) {
        $tick++
        # 1) 대시보드: 프로세스가 없으면 즉시, 있는데 응답이 없으면 FailLimit번 뒤 재시작
        if (Test-Health $HealthUrl 5) {
            $dashFails = 0
        } else {
            $dashFails++
            $procAlive = [bool](Get-StreamlitProcs)
            Write-Log "dashboard: health failed ($dashFails/$FailLimit, process alive=$procAlive)"
            if ($dashFails -ge $FailLimit -or -not $procAlive) {
                Start-Dashboard
                $dashFails = 0
                Start-Sleep -Seconds 15
            }
        }

        # 2) 터널 (대시보드가 살아 있을 때만 판단: 대시보드가 죽으면 터널 health도 실패하므로)
        if (Test-Health $HealthUrl) {
            $url = Get-TunnelUrl
            $alive = [bool](Get-Process -Name cloudflared -ErrorAction SilentlyContinue)
            if (-not $alive -or -not $url) {
                if (-not $alive) { Write-Log "tunnel: process gone" }
                Start-Tunnel | Out-Null
                $tunnelFails = 0
            } elseif ($tick % $TunnelEvery -ne 0) {
                # 공개 주소 점검은 TunnelEvery틱마다
            } elseif (Test-Health "$url/_stcore/health" 20) {
                $tunnelFails = 0
            } else {
                $tunnelFails++
                Write-Log "tunnel: health failed ($tunnelFails/$TunnelFailLimit) $url"
                if ($tunnelFails -ge $TunnelFailLimit) {
                    Start-Tunnel | Out-Null
                    $tunnelFails = 0
                }
            }
        }

        if ($Once) { break }
        Start-Sleep -Seconds $IntervalSec
    }
} finally {
    Write-Log "watchdog: stop"
    $mutex.ReleaseMutex()
}
