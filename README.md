# PocketShell — 口袋里的 DeepSeek 终端 AI 助手

> English: [README.en.md](README.en.md)

> **一个目录装下一切,不碰系统任何地方。** 解压即用、拷走即迁移,
> 不写注册表、不碰 `%APPDATA%`、不改 PATH、零第三方依赖。

PocketShell 是一个面向 Windows 的终端 AI 助手(shell agent),基于 DeepSeek 官方 API:
内置**删除指令硬拦截**等安全防护,通过 token 预算与用量统计控制成本,纯标准库实现、
零依赖、便携自包含——整个项目就是一个目录。

## 特性

- 🪟 **便携自包含**:所有文件(程序 + 配置 + 会话 + 记忆)都在一个目录内,拷走即迁移,
  不污染其它任何目录;另有 **GREEN 绿色版**(内置 Python 3.13),连 Python 都不用装
- 🛡️ **安全铁律**(代码级硬拦截,不依赖模型自觉):
  - 删除/清空/格式化类指令直接 **BLOCK**(`del`/`rm -rf`/`Remove-Item`/`format`/`diskpart clean` 等)
  - 写文件操作(创建/覆盖/移动/重命名/复制)先征求确认(`FILE_WRITE_CONFIRM` 可配)
  - **自毁防护**:禁止 agent 删除/修改自己所在目录
  - 防绕过:`-EncodedCommand`/`iex`/Base64 解码/变量调用/`cmd /c` 嵌套递归分析
- 💰 **省 token**:上下文自动截断预算、工具输出截断、DeepSeek 思考内容仅展示不回存、
  每轮显示 token 消耗与缓存命中
- 🧠 **DeepSeek 原生**:默认 `deepseek-v4-flash`,可切 `deepseek-v4-pro`;
  支持任意 OpenAI 兼容端点(`API_BASE_URL`)
- 🛠️ 7 个轻量工具:shell 执行(带安全层)/ 记忆(remember/recall/forget/update_memory)/
  Bing 搜索 / 网页抓取
- 📦 **零第三方依赖**:纯标准库(urllib/ssl/http.client),不需要 pip 安装任何东西

## 环境要求

- **Python 3.10+**(纯标准库,零依赖;Windows / Linux / macOS 均可)
- 不需要 pip,不需要虚拟环境

## 快速开始

1. 解压到任意位置、**目录名随意**,整个目录自包含、可随时搬家。
2. 配置 API Key(二选一):
   - 设置环境变量 `SGPT_API_KEY=sk-xxx`;或
   - 首次运行自动生成 `config.json`,用编辑器打开,把 `"OPENAI_API_KEY": ""` 改成你的 key。
   - API Key 获取:https://platform.deepseek.com/api_keys
3. 运行(启动脚本不改变你的当前目录):
   ```
   run.bat "查看当前目录"      （Windows）
   ./run.sh "查看当前目录"     （Linux/macOS）
   ```

> 想要连 Python 都不用装?用 **GREEN 绿色版**(内置 Python 3.13 的独立包)。

## 常用命令

> 下面 `python -m pocketshell` 是等效的底层命令,在解压目录的**上一级**执行。

| 命令 | 说明 |
| --- | --- |
| `run.bat "问题"` | 提问(进入**常驻默认会话**,历史保存在 `sessions\default.json`,下次继续) |
| `run.bat --repl` | 连续对话模式 |
| `run.bat --chat NAME "问题"` | 指定独立会话(历史保存于 `sessions\NAME.json`) |
| `run.bat --model deepseek-v4-pro "问题"` | 切换更强模型 |
| `run.bat --max-output 2048 "问题"` | 限制本次回答长度(省 token) |
| `run.bat --no-tools "问题"` | 禁用工具调用(纯问答) |
| `run.bat --no-usage "问题"` | 不显示每轮 token 消耗统计 |
| `run.bat --list-chats` | 列出已有会话 |
| `run.bat --show-chat NAME` | 查看会话历史 |
| `run.bat --clear NAME` | 清除会话(如 `--clear default`) |
| `run.bat --doctor` | 配置健康检查(诊断 API Key 读取问题) |

默认模型 `deepseek-v4-flash`,`--model deepseek-v4-pro` 可切换。

## 装进 PATH:任意目录直接输命令(可自定义命令名)

双击 `setup\install.bat`,它会:

1. **问你想用什么命令名**(直接回车默认 `pocketshell`;想要短命令就填 `g`、`ps` 等,只允许字母/数字/`_`/`-`);
2. 在 `setup\` 里生成对应的 `<命令名>.cmd` 入口;
3. 把 `setup` 目录加入**当前用户**的 PATH(只改用户级注册表,不动系统 PATH)。

然后**新开一个终端**,任意目录输入(以命令名 `g` 为例):

```
g "查看当前目录"
g --repl
g --chat work "继续昨天"
```

> 改名只需重新双击 install.bat 输入新名字(旧 `.cmd` 文件残留可手动删除)。
> 卸载:双击 `setup\uninstall.bat`(只移除 PATH 条目,不删任何文件)。

## 目录结构(全部自包含)

```
pocketshell/
├── run.bat / run.sh          启动脚本
├── pocketshell/              主程序包(纯标准库,零依赖)
│   ├── __main__.py           入口(自动注册包,目录名随意)
│   ├── cli.py                命令行入口
│   ├── api.py                DeepSeek 客户端(流式 + 工具循环 + 用量统计)
│   ├── safety.py             命令安全层(删除硬拦截 + 写文件确认 + 自毁防护)
│   ├── render.py             终端彩色 Markdown 渲染(Windows VT 自动启用)
│   ├── tools.py              工具:shell / 记忆 / 网页抓取 / 搜索
│   ├── session.py            会话持久化 + token 预算
│   └── config.py             配置(env > 文件 > 默认)
├── setup/                    安装/卸载工具(平时用不到,可整个删除)
│   ├── install.bat           双击:问命令名→生成 <名>.cmd→加入用户 PATH
│   ├── uninstall.bat         双击:从用户 PATH 移除(不删文件)
│   ├── install.ps1 / uninstall.ps1   (UTF-8 BOM,中文不乱码)
├── tests/                    测试(可删除)
├── config.json               ★ 唯一配置文件(首次运行自动生成,含全部配置与注释)
├── sessions/                 会话历史(自动创建)
└── memory.txt                长期记忆(remember/recall/forget/update_memory 工具使用)
```

## 安全设计(三层防护)

1. **代码级硬拦截**(`safety.py`,不依赖模型自觉):
   - **BLOCK**(直接拒绝,绝不执行):删除/清空/格式化类指令 —— `del` `erase` `rm` `rmdir` `rd`
     `Remove-Item` `Clear-Content` `Clear-RecycleBin` `format` `Format-Volume`
     `reg delete` `schtasks /delete` `wmic delete` `sc delete` `net user /delete`
     `diskpart clean` `shred` 等;
   - 防绕过:PowerShell `-EncodedCommand`、`Invoke-Expression`、`& $var` 变量调用、
     Base64 解码执行、`cmd /c` 嵌套递归分析;
   - **CONFIRM**(需输入 y 确认):关机/重启、强杀进程、改权限/所有权、卸载软件、改注册表等;
   - **写文件确认**:创建/覆盖/追加/移动/重命名/复制文件都会先征求你的确认
     (`FILE_WRITE_CONFIRM` 开关控制,默认开);记忆文件 `memory.txt` 由记忆工具直接管理,不需要确认;
   - 非交互环境下高危命令默认**拒绝**;
   - **自毁防护**:agent 自身所在目录(程序文件目录)是禁区——删除/清空/移动/重命名/覆盖
     其中的文件一律 BLOCK,防止 agent 把自己改死。
2. **系统提示词约束**:明令禁止删除操作、写文件需确认、禁止绕过手段。
3. **工具输出截断**:shell 结果回传默认截断为 2000 字符,防刷屏省 token。
4. **每轮用量统计**:显示本次消耗的输入/输出 token 与缓存命中(`SHOW_USAGE` 开关,`--no-usage` 可关)。

> 注意:静态分析无法 100% 防御任意混淆,请勿在无备份的重要机器上使用未经审查的提示词。

## 配置:一个 config.json 全管

所有配置集中在根目录的 **`config.json`**(首次运行自动生成带注释的完整模板;
已存在的 config.json 绝不会被覆盖,需重置时手动删除即可)。支持 `//` 注释,可直接用 VS Code 编辑:

```jsonc
{
  // 模型与 API
  "DEFAULT_MODEL": "deepseek-v4-flash",   // 或 deepseek-v4-pro
  "OPENAI_API_KEY": "sk-xxx",             // 必填;也可用环境变量 SGPT_API_KEY
  "API_BASE_URL": "https://api.deepseek.com",

  // 上下文与 token(省 token 关键)
  "CONTEXT_TOKEN_BUDGET": 65536,          // 历史预算,超出自动丢最旧(窗口 1M)
  "MAX_OUTPUT_TOKENS": 4096,              // 单次回答上限(0=不限制)
  "TOOL_OUTPUT_MAX_CHARS": 2000,          // 工具结果回传截断
  "SESSION_MAX_MESSAGES": 100,

  // 采样
  "TEMPERATURE": 0.0,
  "TOP_P": 1.0,

  // 安全
  "CONFIRM_DANGEROUS": true,        // 高危命令(关机/杀进程/卸载等)要求确认
  "FILE_WRITE_CONFIRM": true,       // 写文件(创建/覆盖/移动/重命名/复制)要求确认

  // 工具与交互
  "ENABLE_TOOLS": true,
  "STREAM": true,
  "SHOW_USAGE": true,               // 每轮显示 token 消耗与缓存命中
  "REQUEST_TIMEOUT": 120,

  // 路径(留空则用项目目录下默认,随目录迁移)
  "SESSIONS_DIR": "",
  "MEMORY_FILE": "",

  // 自定义指令(追加到系统提示词末尾,改这里不动代码即可定制行为)
  "CUSTOM_INSTRUCTIONS": ""
}
```

- **优先级**:环境变量 > config.json > 内置默认;环境变量用 `SGPT_`+键名(如 `SGPT_CONTEXT_TOKEN_BUDGET`)
- **生效时机**:修改后重启;命令行参数(`--model`/`--temperature`/`--max-output` 等)临时覆盖配置
- **路径项留空**:自动落在项目目录内(`sessions/`、`memory.txt`),拷走目录即迁移

## 系统提示词在哪里

- **内置提示词**:写在 `pocketshell/api.py` 的 `make_system_prompt()` 函数里
  (安全铁律 + 当前工作目录 + 工具使用规则 + 中文回答风格)。每次运行时动态生成,
  其中的"当前工作目录"会自动刷新。
- **想加自己的要求**:不用改代码——在 `config.json` 里填 `CUSTOM_INSTRUCTIONS`,
  内容会以【用户自定义指令】追加到提示词末尾。内置安全铁律始终保留在前,不会被覆盖。

## 省 token 策略

- **上下文自动管理,无需手动压缩**:历史按 `CONTEXT_TOKEN_BUDGET` 自动丢弃最旧消息
  (保留 system 与最近内容);需要全新上下文时 `--clear default` 或 REPL 中输入 `/clear`。
  REPL 中输入 `/context` 查看实时 token 占用。
- 工具输出截断(默认 2000 字符),防脏输出灌满上下文;
- **DeepSeek 思考内容(reasoning_content)仅展示、绝不回存历史**——V4 默认思考模式,
  思考 token 占比大,不回存使每轮思考只计费一次;
- 记忆外置到 `memory.txt`,按需 recall,不常驻上下文;
- 仅 7 个轻量工具,工具 schema 本身占用的 token 远小于重型 agent。

## GREEN 绿色版(内置 Python,⚠️ 仅 Windows)

`pocketshell-green.zip` 内置 **Windows 版 Python 3.13.15**(官方 embeddable 包),
机器上不需要装任何东西:不用装 Python、不用 pip、不写注册表、不改系统 PATH。启动用 `green.bat`。

- **体积说明**:green 包约 **10.6MB(zip)/ 40MB(解压)**,比便携版(45KB)大得多——
  因为它把整个 Python 解释器塞进去了,这是**设计使然**,体积换"零安装"。
  便携版只需系统装有 Python 3.10+,两者功能完全一致。
- **⚠️ 仅限 Windows**:内置的是 Windows 版 Python,Linux / macOS 请使用**便携版**
  (`pocketshell.zip` / 源码),Linux 大多自带 Python,直接跑即可,不需要 green 包。
- 内置 Python 版本固定,需要新版本时重新下载本包。

## 开发与测试

```bash
cd pocketshell                 # 仓库根
HOME=$(mktemp -d) python3 -m pytest tests/ -q
```

## License

[Mozilla Public License Version 2.0](LICENSE)
