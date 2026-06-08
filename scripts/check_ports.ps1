# 检查 a2a-prod 使用的端口（4000 / 12001-12003）是否被占用。
# 用法：
#   .\scripts\check_ports.ps1
#
# 退出码：
#   0  所有端口空闲
#   1  有端口被占用

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# 与 ARCHITECTURE.md 端口规划表对齐
$ports = @(
    @{ Port = 4000;  Name = "LiteLLM Proxy" },
    @{ Port = 12001; Name = "GLM Agent" },
    @{ Port = 12002; Name = "DeepSeek Agent" },
    @{ Port = 12003; Name = "MiniMax Agent" }
)

$occupied = @()

foreach ($entry in $ports) {
    $port = $entry.Port
    $name = $entry.Name

    # Get-NetTCPConnection 查 LISTENING 状态
    $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue

    if ($null -eq $conn) {
        Write-Host ("[ OK ] {0,5} ({1}) 空闲" -f $port, $name) -ForegroundColor Green
    }
    else {
        $procId = $conn[0].OwningProcess
        try {
            $proc = Get-Process -Id $procId -ErrorAction Stop
            $procName = $proc.ProcessName
        }
        catch {
            $procName = "PID=$procId (进程已退出或无权限)"
        }
        Write-Host ("[FAIL] {0,5} ({1}) 被占用：{2}" -f $port, $name, $procName) -ForegroundColor Red
        $occupied += $entry
    }
}

if ($occupied.Count -gt 0) {
    Write-Host ""
    Write-Host ("共 {0} 个端口被占用，请释放后再启动 docker compose。" -f $occupied.Count) -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "所有端口空闲，可以启动服务。" -ForegroundColor Green
exit 0
