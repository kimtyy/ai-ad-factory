<#
scripts/watchdog.ps1 - 대시보드(Streamlit 8501) + 모바일 터널(cloudflared) 감시·자동 복구

- 30초마다 /_stcore/health 확인 → 3번 연속 실패(약 90초)하면 Streamlit 재시작
  (렌더 중에도 health는 응답하므로 렌더를 크래시로 오인하지 않음)
- 터널: cloudflared 프로세스가 없거나 공개 주소 health가 3번 연속 실패하면 재시작
  (임시 터널은 Cloudflare가 몇 시간 뒤 삭제함: "Tunnel not found") → 새 주소를 outputs\tunnel_url.txt에 기록
- 서버·터널은 독립 콘솔(cmd /c)로 띄움: 감시 창·도구 창이 닫혀도 같이 죽지 않게
- 중복 실행 방지: 이미 감시 중이면 바로 종료 (Global 뮤텍스)
- 로그: outputs\watchdog.log

수동 실행: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog.ps1
자동 실행: scripts\install_watchdog.ps1 (Windows 로그인 시 작업 스케줄러로 시작)
#>
param(
    [int]$IntervalSec = 30,
    [int]$FailLimit = 3,
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

Write-Log "watchdog: start (interval ${IntervalSec}s, fail limit $FailLimit)"
$dashFails = 0
$tunnelFails = 0
try {
    while ($true) {
        # 1) 대시보드
        if (Test-Health $HealthUrl) {
            $dashFails = 0
        } else {
            $dashFails++
            Write-Log "dashboard: health failed ($dashFails/$FailLimit)"
            if ($dashFails -ge $FailLimit -or -not (Get-StreamlitProcs)) {
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
                Start-Tunnel | Out-Null
                $tunnelFails = 0
            } elseif (Test-Health "$url/_stcore/health" 20) {
                $tunnelFails = 0
            } else {
                $tunnelFails++
                Write-Log "tunnel: health failed ($tunnelFails/$FailLimit) $url"
                if ($tunnelFails -ge $FailLimit) {
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
