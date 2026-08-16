# 想法记录 (Ideas)

本文件记录 PocketShell 的未来功能想法,不承诺实现顺序。

## 2026-08-17 — `-setworkspace` 工作目录指令(用户 zhang 提出)

**想法**:新增指令 `-setworkspace`,把当前目录设定为 agent 的「工作目录」。

**行为**:
- 设定后,agent 可**直接修改工作目录内的文件,无需逐次确认**(放开 FILE_WRITE_CONFIRM)
- **删除文件仍需用户动手**:工作目录内的删除类操作依然硬拦截(BLOCK),必须用户自己删

**设计要点**:
- 配置存储:config.json 新增 `WORKSPACE_DIR` 键(空 = 未启用),或存进会话
- `safety.py` 改造:对命令做路径解析,判断目标是否在 WORKSPACE_DIR 内
  - workspace 内 + 写操作(新建/覆盖/重命名等)→ 自动放行,不再弹 CONFIRM
  - 任何删除类操作(rm / del / Remove-Item / rmdir 等)→ 维持 BLOCK,提示「请用户手动删除」
  - workspace 外 → 维持现状(写 CONFIRM,删 BLOCK)
- CLI:新增 `-setworkspace` 参数;不带值时取当前 cwd;`-setworkspace off` 关闭
- **授权生命周期(补充,zhang 2026-08-17)**:workspace 授权绑定「会话 + 当前目录」,**不是永久生效**——
  - 用户**关闭 cmd 窗口** → 授权失效,下次启动需重新 `-setworkspace`
  - 用户 **cd 离开该目录** → 授权立即失效,回到目录也需重新授权(以 cwd != WORKSPACE_DIR 为准)
  - 实现:WORKSPACE_DIR 存**内存/会话文件**,不写进持久 config.json(避免跨窗口残留);每次执行工具前校验 os.getcwd() 是否仍等于 WORKSPACE_DIR,不等则视为未授权
- 安全语义:workspace 是用户显式授权的地盘,写权限放开但删除永远锁死——守住「绝对安全」底线
