# uninstall.ps1 - PocketShell uninstaller
# Removes the setup folder from CURRENT USER's PATH. Deletes NO files.
$ErrorActionPreference = 'Stop'
$setupDir = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\\')
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $p) {
    Write-Host '[PocketShell] 用户 PATH 为空，无需卸载。'
    exit
}
$items = @($p -split ';' | Where-Object { $_ -ne '' -and $_ -ne $setupDir })
[Environment]::SetEnvironmentVariable('Path', ($items -join ';'), 'User')
Write-Host ('[PocketShell] 已从用户 PATH 移除：' + $setupDir)
Write-Host '生成的 .cmd 命令文件仍保留在 setup 目录，如需删除请手动删除。'
