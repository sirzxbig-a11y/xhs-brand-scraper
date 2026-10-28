# 用「远程调试端口」启动真实 Chrome，供本项目「连接真实浏览器（CDP）」模式使用。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\start_chrome_debug.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_chrome_debug.ps1 -UseMainProfile
#
# 说明：
#   - 默认使用独立配置目录 %LOCALAPPDATA%\XhsScraperChrome，不影响你日常 Chrome。
#   - 加 -UseMainProfile 则使用你日常的 Chrome 配置（指纹最真实，但必须先完全退出 Chrome）。
param(
    [int]$Port = 9222,
    [switch]$UseMainProfile
)

$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host "未找到 Google Chrome，请先安装 Chrome。" -ForegroundColor Red
    exit 1
}

if ($UseMainProfile) {
    $running = Get-Process chrome -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "检测到 Chrome 正在运行。使用 -UseMainProfile 前请先完全退出 Chrome（含托盘图标）。" -ForegroundColor Yellow
        exit 1
    }
    $profile = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
} else {
    $profile = Join-Path $env:LOCALAPPDATA "XhsScraperChrome"
}

New-Item -ItemType Directory -Force -Path $profile | Out-Null
Start-Process $chrome -ArgumentList "--remote-debugging-port=$Port", "--user-data-dir=`"$profile`""

Write-Host "已用调试端口 $Port 启动 Chrome。" -ForegroundColor Green
Write-Host "配置目录：$profile"
Write-Host "请在这个 Chrome 里登录小红书，然后回到 http://127.0.0.1:8000 点「打开浏览器登录」。"
Write-Host "（后端启动前请设置环境变量：`$env:XHS_CDP=`"http://127.0.0.1:$Port`"）"
