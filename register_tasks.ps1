
$ErrorActionPreference = 'Stop'
$py = 'D:\4.Study\Env\Python\pythonw.exe'
if (-not (Test-Path $py)) { $py = 'pythonw.exe' }
$script = 'E:\Sunset\sunset_glow.py'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

# 任务1：每 6 小时常规检查
$action1 = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '" --mode digest') -WorkingDirectory 'E:\Sunset'
$trigger1 = New-ScheduledTaskTrigger -Once -At 06:12 -RepetitionInterval (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName 'SunsetGlow_Digest' -Action $action1 -Trigger $trigger1 -Settings $settings -Description '晚霞预报：每6小时检查一次，仅在高分时推送' -Force | Out-Null

# 任务2：下午 14:00-20:00 每小时检查一次日落前窗口（脚本内部去重，每天最多加推一次）
$action2 = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '" --mode alert') -WorkingDirectory 'E:\Sunset'
$trigger2 = New-ScheduledTaskTrigger -Daily -At 14:00
$trigger2.Repetition = (New-ScheduledTaskTrigger -Once -At 14:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 6)).Repetition
Register-ScheduledTask -TaskName 'SunsetGlow_PreSunset' -Action $action2 -Trigger $trigger2 -Settings $settings -Description '晚霞预报：日落前加推检查（下午每小时，窗口内且高分才推）' -Force | Out-Null

Write-Output 'TASKS_REGISTERED'
