# Changelog

本项目的所有重要变更都会记录在此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [未发布]

### 修复

- **Ctrl+C 取消不再丢上下文**:此前 `session.save()` 只在对话正常结束时调用,生成中
  按 Ctrl+C 中断会直接退出进程,已追加的用户问题与工具调用链从未落盘,下次启动全部
  丢失。现在取消时先把当前上下文写入会话文件再退出;REPL 模式下取消本轮后**继续
  留在 REPL** 可再次提问,上下文完整保留(含被取消那轮的用户问题)。

### 改进

- **系统提示词重构为「任务方法论」**:从禁令清单式(9 条工具规则堆砌)改为
  「完成任务的标准流程(勘察→规划→执行→验证→汇报)+ 工具选择 + 失败处理 +
  回答规范」三段结构。核心变化:强调"第一步列当前目录看到真实文件"、"查信息
  先 web_search 再 fetch_url、不要 curl 存本地"、"同一目标失败 2 次必须停止换
  方向"、"汇报要说明做了什么/结果如何/失败原因与建议"。安全铁律与 shell 语法
  提示保留,token 开销仅增约 9%。

### 修复

- **工具循环硬上限(防烧 token 螺旋)**:单次对话工具调用轮数超过
  `MAX_TOOL_ROUNDS`(默认 10,可配置)即停止并如实汇报,不再无限重试/换姿势硬试。
- **连接中断自动重试**:API 流式连接被重置(`WinError 10054` 等,未输出任何内容时)
  自动重试;已输出部分内容时明确报错提示重试,避免重复输出。
- **下载写文件纳入确认**:`curl -o/-O`、`wget -O/-o`、`--output`、
  `Invoke-WebRequest -OutFile` 等下载保存到本地文件的命令现在同样需要用户确认
  (此前会绕过写文件检查,模型借 curl 把网页 HTML 直接写进用户目录)。
- **提示词防螺旋规则**:同一目标连续失败 2 次必须停止换方向;写文件被用户拒绝后
  不得换写法反复尝试;查看网页一律用 fetch_url 不要 curl 存本地。

## [0.2.2] - 2026-08-19

### 修复

- **chcp 65001 恢复在全部入口脚本**:用户实机验证这是 ffmpeg 等程序处理中文
  文件名的关键(见下方 [未发布] 详细说明);green 版 `green.bat` 与 green 版
  `install.ps1` 命令模板同步补齐,便携版与 green 版行为完全一致。
- README 新增「中文文件名与编码」说明。

## [未发布]

### 修复

- **shell 检测重写**:旧实现用 PSModulePath 段数判定,cmd 用户会被误判成 PowerShell,
  导致提示词说一套、实际执行另一套,模型在两种语法间盲猜(如 `g 转换回马喷.mp3为wav`
  任务 20+ 轮失败)。现改为检测**当前控制台宿主进程**(GetConsoleProcessList +
  QueryFullProcessImageNameW):在 cmd 中启动就用 cmd 语法,在 PowerShell 中启动就用
  PowerShell 语法;系统提示词、`execute_shell_command` 工具描述同步按检测结果动态生成。
- **路径幻觉约束(重写)**:此前用"代码拦截盘符绝对路径"治路径幻觉,实测误伤正确的
  绝对路径(模型猜的 `D:\下载\回马喷.mp3` 可能真实存在),反而逼模型改用 `..\..` 相对
  路径反复探测。现已**删除代码拦截**,改为纯提示词约束:第一步永远是列当前工作目录
  (`dir /b`/`Get-ChildItem`)看到真实文件名再动手,任务文件默认就在 cwd 里、不要猜路径、
  不要 cd 去别的目录;绝对路径仅用户明确给出时使用。
- **cmd 语法警告**:多条命令用 `&` 连接(不是 `;`,那是 PowerShell 分隔符);不要用
  PowerShell 的 `Select-Object`/`Get-ChildItem` 语法。
- **chcp 65001 加回入口脚本(用户实测正解)**:此前以为 chcp 触发假清屏而移除,
  导致 ffmpeg 打开中文文件名报 `Illegal byte sequence`。用户实机验证:**把
  `chcp 65001 >nul` 加回启动脚本后一切正常**(控制台代码页切 UTF-8,cmd 传给
  程序的中文参数不再按 GBK 转坏)。现已在 `run.bat` / `install.ps1` 生成的
  `.cmd` 模板 / green 版 `green.bat` 模板全部加回。之前"chcp 无效、需换 ffmpeg
  或开系统 UTF-8"的判断是错误的,特此更正。

### 已规划

- 工具 schema 精简(约省 40% 每请求固定开销,`tools.py` 的 description 冗余)

## [0.2.1] - 2026-08-18

### 修复

- **启动即"清屏"**:入口脚本里的 `chcp 65001` 会触发终端重绘整个屏幕缓冲区,
  Windows Terminal / 新版 conhost 下表现为运行命令后命令行和之前的内容全部消失。
  已将 chcp 从 `run.bat` / `g.cmd` 生成模板 / `green.bat` 全部移除,
  改为 Python 进程内 `SetConsoleOutputCP(65001)`(UTF-8 效果相同,不触碰终端显示缓冲)。

## [0.2.0] - 2026-08-17

### 新增

- **用量显示输入构成拆分**:每轮输出 `提示词注入 + 上下文累计 + 本轮新输入(估算)+ 输出`,
  并显示请求次数(含工具循环)与缓存命中/未命中 —— 一眼看清 token 花在哪
- **`-setworkspace` 工作目录授权**:把当前目录设为工作目录,其内写文件免确认
  (删除仍硬拦);关闭窗口或 `cd` 离开自动失效
- **系统提示词隔次注入**:`SYSTEM_PROMPT_INTERVAL`(默认 3)每 N 轮注入一次 system,
  中间轮次靠上下文缓存,10 轮对话 system 开销省约 84%
- **config 缺失键自动合并**:升级后老 `config.json` 自动补齐新增配置项,不覆盖已有值

### 修复

- `Format-List` 等 PowerShell 输出 cmdlet 不再被误判为磁盘格式化
  (`Format-Volume` 等真实格式化指令仍拦截)
- cwd 变化检测改为整段提示词比较,修复"cd 到父目录"漏检

### 清理

- 环境变量前缀统一为 `PS_`,彻底移除全部 `SGPT_` / sgptrc 残留
- 清理测试文件中的旧路径引用

## [0.1.0] - 2026-08-16

### 新增

- 首个公开版本:便携 DeepSeek 终端 AI 助手,一个目录装下一切
- **安全层**(`safety.py`):删除/清空/格式化指令硬拦截、防绕过
  (`-EncodedCommand`/`iex`/Base64/变量调用/`cmd /c` 递归分析)、
  写文件确认、自毁防护
- 7 个轻量工具:shell 执行(带安全层)/ 记忆(remember/recall/forget/update_memory)/
  Bing 搜索(RSS,大陆可访问)/ 网页抓取
- 纯标准库实现(urllib/ssl/http.client),零第三方依赖
- 会话持久化 + token 预算截断 + 系统提示词按频率注入
- 彩色 Markdown 流式渲染(Windows VT 自动启用)
- **GREEN 绿色版**:内置 Windows Python 3.13,连 Python 都不用装
- `install.bat` 自定义命令名装入 PATH(可选)
- 中英双语 README、MPL-2.0 许可

[未发布]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zzzjjj-ss/PocketShell/releases/tag/v0.1.0
