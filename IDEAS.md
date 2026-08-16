# 想法记录 (Ideas)

本文件记录 PocketShell 的未来功能想法,不承诺实现顺序。

## ✅ 2026-08-17 — `-setworkspace` 工作目录指令(用户 zhang 提出)—— 已实现(commit 待推)

**想法**:新增指令 `-setworkspace`,把当前目录设定为 agent 的「工作目录」。

**行为**:
- 设定后,agent 可**直接修改工作目录内的文件,无需逐次确认**(放开 FILE_WRITE_CONFIRM)
- **删除文件仍需用户动手**:工作目录内的删除类操作依然硬拦截(BLOCK),必须用户自己删

**实现对照**(全部落地):
- 配置存储:WORKSPACE_DIR 为 safety.py 模块级内存变量,**不落盘**(符合"关闭窗口即失效")
- `safety.py`:`set_workspace/get_workspace/is_workspace_active/is_workspace_write/extract_write_target/_cmdlet_write_target/_path_is_within`;支持 cmd 重定向 >/>> 与 PowerShell Set-Content/Add-Content/Out-File/New-Item/Copy-Item/Move-Item/Rename-Item;Windows 盘符路径判外
- `tools.py` `_execute_shell`:CONFIRM + category=="write" 且 `is_workspace_write()` → need_confirm=False 免确认
- `cli.py`:新增 `-setworkspace [DIR|off]` 参数(不带值=当前 cwd);REPL 内 `/setworkspace [DIR|off]`
- `api.py` make_system_prompt 安全铁律第 7 条:告知模型工作目录内写操作免确认、删除仍禁
- 测试:pocketshell/tests/test_workspace.py 19 个用例,全仓 143 passed
- **授权生命周期(zhang 补充,已实现)**:授权绑定「会话+当前目录」——关闭窗口进程退出即失效;cd 离开(cwd != WORKSPACE_DIR)立即失效,回到目录也需重新授权
- 安全语义:写权限放开,删除永远锁死——守住「绝对安全」底线
