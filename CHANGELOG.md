# Changelog

本项目的所有重要变更都会记录在此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [未发布]

### 修复

- **shell 检测重写**:旧实现用 PSModulePath 段数判定,cmd 用户会被误判成 PowerShell,
  导致提示词说一套、实际执行另一套,模型在两种语法间盲猜(如 `g 转换回马喷.mp3为wav`
  任务 20+ 轮失败)。现改为检测**当前控制台宿主进程**(GetConsoleProcessList +
  QueryFullProcessImageNameW):在 cmd 中启动就用 cmd 语法,在 PowerShell 中启动就用
  PowerShell 语法;系统提示词、`execute_shell_command` 工具描述同步按检测结果动态生成。
- **路径幻觉约束**:提示词强调任务文件默认在当前工作目录(cwd 已注入)、直接用相对路径、
  严禁编造盘符绝对路径;代码层兜底——工具参数含盘符绝对路径(如 `D:\xxx\file.mp3`)
  时直接拦截,提示改用相对路径或先 `dir /b` 列目录,命令失败先看错误输出而非反复盲试。

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

[未发布]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zzzjjj-ss/PocketShell/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zzzjjj-ss/PocketShell/releases/tag/v0.1.0
