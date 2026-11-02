<#
.SYNOPSIS
  Автозапуск Homealize при входе в Windows, без окон. Задача «Homealize» запускает `homealize serve`
  (прогоны и Telegram-бот), задача «Homealize Dashboard» — `homealize dashboard` (веб-дашборд).
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
$Tasks = [ordered]@{
    "Homealize"           = @{ Command = "serve"; Description = "Homealize: прогоны 999.md по расписанию и Telegram-бот" }
    "Homealize Dashboard" = @{ Command = "dashboard"; Description = "Homealize: веб-дашборд (http://localhost:8501)" }
}
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Install -eq $Remove) {
    Write-Host "Укажите -Install (включить автозапуск) или -Remove (выключить)."
    exit 2
}

if ($Remove) {
    foreach ($name in $Tasks.Keys) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Задача «$name» остановлена и удалена."
        } else {
            Write-Host "Задачи «$name» нет — удалять нечего."
        }
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

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$options = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
foreach ($name in $Tasks.Keys) {
    $task = $Tasks[$name]
    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m homealize.cli $($task.Command)" `
        -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $options `
        -Principal $principal -Description $task.Description -Force | Out-Null
    Start-ScheduledTask -TaskName $name
}
Write-Host "Готово: serve и дашборд запущены и будут стартовать при каждом входе в Windows."
Write-Host "Бот пришлёт «Homealize запущен». Дашборд на этом компьютере: http://localhost:8501"
$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress
foreach ($address in $addresses) {
    Write-Host "С телефона в той же Wi-Fi: http://${address}:8501"
}
Write-Host "Логи: $Root\logs\homealize.log (serve) и $Root\logs\dashboard.log (дашборд)"
