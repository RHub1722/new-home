<#
.SYNOPSIS
  Автозапуск Homealize при входе в Windows: задача Планировщика «Homealize» запускает `homealize serve` без окна.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "Homealize"
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Install -eq $Remove) {
    Write-Host "Укажите -Install (включить автозапуск) или -Remove (выключить)."
    exit 2
}

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача «$TaskName» удалена, serve остановлен."
    } else {
        Write-Host "Задачи «$TaskName» нет — удалять нечего."
    }
    exit 0
}

if (-not (Test-Path $Pythonw)) {
    Write-Host "Не найден $Pythonw — выполните в корне проекта: uv sync"
    exit 1
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "Нет файла .env с TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID — см. README, раздел «Telegram»."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m homealize.cli serve" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$options = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $options `
    -Principal $principal -Description "Homealize: прогоны 999.md по расписанию и Telegram-бот" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Готово: serve запущен и будет стартовать при каждом входе в Windows."
Write-Host "Бот пришлёт «Homealize запущен». Логи: $Root\logs\homealize.log"
