# uninstall.ps1 - remove this agent folder from the CURRENT USER's PATH
$ErrorActionPreference = 'Stop'
$d = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\')
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $p) {
    Write-Host '[agent] 用户 PATH 为空，无需卸载。'
    exit
}
$items = @($p -split ';' | Where-Object { $_ -ne '' -and $_ -ne $d })
[Environment]::SetEnvironmentVariable('Path', ($items -join ';'), 'User')
Write-Host ('[agent] 已从用户 PATH 移除：' + $d)
