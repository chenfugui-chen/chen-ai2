<#
一键部署脚本：把本地代码同步到阿里云服务器，并重建容器

用法（在项目根目录执行）：
    .\deploy.ps1                 # 同步代码 + 重建重启容器
    .\deploy.ps1 -SkipRestart    # 只同步代码，不在服务器上重启

说明：
- 只同步「代码」；服务器上的数据不会被覆盖，保持原样：
    .env / chroma_db / md5.txt / profile / 资料
- 依赖已配置好的 SSH 免密登录
- 服务器地址不写在脚本里，通过环境变量提供（避免把 IP 提交进仓库）：
    $env:AIRAG_SERVER    = "user@your-server-ip"   # 必填
    $env:AIRAG_REMOTE_DIR = "/root/aiRAG"          # 选填，默认 /root/aiRAG
    $env:AIRAG_PUBLIC_URL = "http://your-server-ip" # 选填，仅用于结束时的提示
#>
param(
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"

$Server = $env:AIRAG_SERVER
if (-not $Server) {
    Write-Host "[ERROR] 未设置环境变量 AIRAG_SERVER，请先执行：`$env:AIRAG_SERVER = `"user@your-server-ip`"" -ForegroundColor Red
    exit 1
}

$RemoteDir = if ($env:AIRAG_REMOTE_DIR) { $env:AIRAG_REMOTE_DIR } else { "/root/aiRAG" }
$PublicUrl = if ($env:AIRAG_PUBLIC_URL) { $env:AIRAG_PUBLIC_URL } else { "http://$($Server.Split('@')[-1])" }

# 需要同步的代码目录
$Dirs = @("agent", "tools", "index", "document", "deploy", "docker", ".streamlit")

# 需要同步的代码文件（软链接/新增/修改都靠这个清单）
$Files = @(
    "app_web.py", "api_server.py", "config_data.py", "history_store.py",
    "knowledge_base.py", "memory_store.py", "vector_stores.py", "import_kb.py",
    "requirements.txt", "Dockerfile", "docker-compose.yml", ".dockerignore",
    "README.md", ".env.example"
)

$Local = $PSScriptRoot
Set-Location $Local

Write-Host "===== 1/3 同步代码目录 =====" -ForegroundColor Cyan
foreach ($d in $Dirs) {
    $src = Join-Path $Local $d
    if (Test-Path $src) {
        Write-Host "  ↑ $d/"
        scp -r $src "${Server}:${RemoteDir}/"
    } else {
        Write-Host "  (跳过：本地不存在 $d/)" -ForegroundColor DarkYellow
    }
}

Write-Host "===== 2/3 同步代码文件 =====" -ForegroundColor Cyan
$srcFiles = @()
foreach ($f in $Files) {
    $p = Join-Path $Local $f
    if (Test-Path $p) {
        Write-Host "  ↑ $f"
        $srcFiles += $p
    }
}
if ($srcFiles.Count -gt 0) {
    scp $srcFiles "${Server}:${RemoteDir}/"
}

Write-Host "===== 清理服务器上的 __pycache__（避免旧字节码） =====" -ForegroundColor Cyan
ssh $Server "find $RemoteDir -name __pycache__ -type d -prune -exec rm -rf {} + ; true"

if ($SkipRestart) {
    Write-Host "`n[OK] 代码已同步（未重启服务器容器）" -ForegroundColor Green
    exit 0
}

Write-Host "===== 3/3 服务器重建并重启容器 =====" -ForegroundColor Cyan
ssh $Server "cd $RemoteDir && docker compose up -d --build app"

Write-Host "`n[OK] 部署完成，可访问 $PublicUrl（宿主机 Nginx，需 Basic Auth）" -ForegroundColor Green