<#
scripts/install_calendar_schedule.ps1 - 구글 캘린더 자동 제작을 Windows 작업 스케줄러에 등록 (관리자 권한 불필요)

  등록:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_calendar_schedule.ps1              # 10분마다
         powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_calendar_schedule.ps1 -EveryMinutes 15
  해제:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_calendar_schedule.ps1 -Uninstall
  지금 실행: Start-ScheduledTask -TaskName AdFactoryCalendar   / 로그: outputs\scheduler\runner.log
#>
param([int]$EveryMinutes = 10, [switch]$Uninstall)

$TaskName = "AdFactoryCalendar"
$Root = Split-Path $PSScriptRoot -Parent
$Runner = Join-Path $PSScriptRoot "calendar_runner.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed: $TaskName"
    exit 0
}

$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$Runner`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
# 렌더는 길면 수 분: 2시간 넘으면 중단, 이전 실행이 돌고 있으면 새 실행은 건너뜀 (러너도 잠금 파일로 한 번 더 막음)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "AI 광고 공장: 'AI 광고 공장' 구글 캘린더 일정 → 대본·렌더 → 비공개 업로드·첫 댓글 대기열 → 일정 설명란에 결과 기록" -Force | Out-Null
Write-Output "registered: $TaskName (every $EveryMinutes min)"
