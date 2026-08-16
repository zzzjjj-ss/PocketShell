# install.ps1 - add this agent folder to the CURRENT USER's PATH
# Saved as UTF-8 with BOM so Windows PowerShell 5.1 reads Chinese correctly.
$ErrorActionPreference = 'Stop'
$d = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\')
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $p) { $p = '' }
$items = @($p -split ';' | Where-Object { $_ -ne '' })
if ($items -contains $d) {
    Write-Host '[agent] 已存在于 PATH，无需重复添加。'
} else {
    $items += $d
    [Environment]::SetEnvironmentVariable('Path', ($items -join ';'), 'User')
    Write-Host ('[agent] 已加入用户 PATH：' + $d)
}
Write-Host '请新开一个终端，然后输入 agent 试试。'
