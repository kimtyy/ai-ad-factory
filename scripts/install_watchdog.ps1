<#
scripts/install_watchdog.ps1 - 감시 스크립트를 Windows 로그인 시 자동 시작으로 등록 (작업 스케줄러, 관리자 권한 불필요)

  등록:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1
  해제:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1 -Uninstall
  상태:  Get-ScheduledTask -TaskName AdFactoryWatchdog ; outputs\watchdog.log
#>
param([switch]$Uninstall)

$TaskName = "AdFactoryWatchdog"
$Script = Join-Path $PSScriptRoot "watchdog.ps1"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed: $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# 감시 스크립트 자체가 죽으면 1분 뒤 다시 (최대 999회), 실행 시간 제한 없음, 배터리여도 실행
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "AI 광고 공장: 대시보드(8501)·모바일 터널 감시 및 자동 재시작" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "registered and started: $TaskName"
