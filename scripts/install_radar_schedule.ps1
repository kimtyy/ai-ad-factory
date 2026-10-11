<#
scripts/install_radar_schedule.ps1 - 떡상 레이더 정기 수집을 Windows 작업 스케줄러에 등록 (관리자 권한 불필요)

  등록:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_radar_schedule.ps1            # 매일 07:00
         powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_radar_schedule.ps1 -At 05:30
  해제:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_radar_schedule.ps1 -Uninstall
  지금 실행: Start-ScheduledTask -TaskName AdFactoryRadar   / 로그: outputs\radar\radar_daily.log
#>
param([string]$At = "07:00", [switch]$Uninstall)

$TaskName = "AdFactoryRadar"
$Cmd = Join-Path $PSScriptRoot "radar_daily.cmd"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed: $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Cmd`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# PC가 꺼져 있어 놓친 실행은 켜졌을 때 바로, 1시간 넘게 걸리면 중단, 배터리여도 실행
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "AI 광고 공장: 떡상 레이더 매일 수집 + 트렌드 다이제스트 (업로드 제목·설명·해시태그에 자동 주입)" -Force | Out-Null
Write-Output "registered: $TaskName (daily $At)"
