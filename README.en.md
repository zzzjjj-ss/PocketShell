# PocketShell — a DeepSeek terminal AI assistant that lives in one folder

> **Everything in one folder. Touches nothing else.** Unzip and run; copy the folder and it moves.
> No registry writes, no `%APPDATA%`, no PATH changes, zero third-party dependencies.

PocketShell is a terminal AI assistant (shell agent) for Windows, built on the DeepSeek API:
hard **deletion guard** built into the safety layer, token-budgeted context to keep costs low,
pure standard library, zero dependencies, fully portable and self-contained — the whole project
is a single directory.

## Features

- 🪟 **Portable & self-contained**: program, config, sessions and memory all live in one folder;
  copy it anywhere and it just works. A **GREEN edition** (bundled Python 3.13) runs without even
  installing Python.
- 🛡️ **Safety rules** (enforced in code, not by prompt):
  - Delete / clear / format commands are **BLOCKED** (`del`, `rm -rf`, `Remove-Item`, `format`,
    `diskpart clean`, and more)
  - File writes (create/overwrite/move/rename/copy) ask for confirmation (`FILE_WRITE_CONFIRM`)
  - **Self-destruction guard**: the agent can never delete or modify its own folder
  - Bypass attempts blocked: `-EncodedCommand`, `iex`, Base64, variable indirection,
    nested `cmd /c` analysis
- 💰 **Token savings**: auto-truncated context budget, truncated tool output, DeepSeek reasoning
  content shown but never re-sent, per-turn token usage + cache-hit display
- 🧠 **DeepSeek native**: default `deepseek-v4-flash`, switch to `deepseek-v4-pro`;
  any OpenAI-compatible endpoint via `API_BASE_URL`
- 🛠️ 7 lightweight tools: shell (guarded) / memory (remember/recall/forget/update_memory) /
  Bing search / web fetch
- 📦 **Zero third-party deps**: pure stdlib (`urllib`/`ssl`/`http.client`), no pip install needed

## Requirements

- **Python 3.10+** (pure stdlib, zero deps; Windows / Linux / macOS)
- No pip, no virtualenv required

## Quick start

1. Unzip anywhere — **the folder name doesn't matter**. The whole folder is self-contained.
2. Configure your API key (either):
   - Set env var `SGPT_API_KEY=sk-xxx`; or
   - On first run `config.json` is generated automatically — open it and fill in
     `"OPENAI_API_KEY": ""`.
   - Get a key: https://platform.deepseek.com/api_keys
3. Run (the launcher does not change your current directory):
   ```
   run.bat "show current directory"      (Windows)
   ./run.sh "show current directory"     (Linux/macOS)
   ```

> Don't want to install Python at all? Use the **GREEN edition** (bundled Python 3.13).

## Common commands

> `python -m pocketshell` below is the equivalent low-level command, run from the
> parent directory of the extracted folder.

| Command | Description |
| --- | --- |
| `run.bat "question"` | Ask (persistent default session in `sessions\default.json`) |
| `run.bat --repl` | Continuous chat mode |
| `run.bat --chat NAME "question"` | Separate named session (`sessions\NAME.json`) |
| `run.bat --model deepseek-v4-pro "question"` | Use the stronger model |
| `run.bat --max-output 2048 "question"` | Cap answer length (saves tokens) |
| `run.bat --no-tools "question"` | Disable tools (pure Q&A) |
| `run.bat --no-usage "question"` | Hide per-turn token stats |
| `run.bat --list-chats` | List sessions |
| `run.bat --show-chat NAME` | View session history |
| `run.bat --clear NAME` | Clear a session (e.g. `--clear default`) |
| `run.bat --doctor` | Config health check (diagnose API key issues) |

Default model: `deepseek-v4-flash`. Switch with `--model deepseek-v4-pro`.

## Install into PATH: use your own command name

Double-click `setup\install.bat`:

1. It asks for a **command name** (Enter for default `pocketshell`; use `g`/`ps` for a short one —
   letters/digits/`_`/`-` only);
2. Generates `<name>.cmd` in `setup\`;
3. Adds the `setup` folder to the **current user's** PATH (user-level registry only).

Then open a **new terminal** and type (example with `g`):

```
g "show current directory"
g --repl
g --chat work "continue yesterday"
```

To rename, just run install.bat again with a new name (old `.cmd` leftovers can be deleted manually).
Uninstall: double-click `setup\uninstall.bat` (removes the PATH entry only, deletes no files).

## Directory layout (fully self-contained)

```
pocketshell/
├── run.bat / run.sh          launchers
├── pocketshell/              main package (pure stdlib, zero deps)
│   ├── __main__.py           entry (auto-registers the package, folder name free)
│   ├── cli.py                CLI entry
│   ├── api.py                DeepSeek client (streaming + tool loop + usage stats)
│   ├── safety.py             safety layer (delete guard + write confirm + self-dir guard)
│   ├── render.py             terminal colored Markdown (Windows VT auto-enabled)
│   ├── tools.py              tools: shell / memory / web fetch / search
│   ├── session.py            session persistence + token budget
│   └── config.py             config (env > file > default)
├── setup/                    install/uninstall tools (safe to delete)
│   ├── install.bat           double-click: ask name -> generate <name>.cmd -> add user PATH
│   ├── uninstall.bat         double-click: remove from user PATH (no file deletion)
│   ├── install.ps1 / uninstall.ps1   (UTF-8 BOM so Chinese renders correctly)
├── tests/                    tests (safe to delete)
├── config.json               ★ the one config file (auto-created with comments)
├── sessions/                 session history (auto-created)
└── memory.txt                long-term memory (remember/recall/forget/update_memory)
```

## Safety design (layered)

1. **Hard code-level guard** (`safety.py`, not model goodwill):
   - **BLOCK** (refused, never executed): `del` `erase` `rm` `rmdir` `rd` `Remove-Item`
     `Clear-Content` `Clear-RecycleBin` `format` `Format-Volume` `reg delete`
     `schtasks /delete` `wmic delete` `sc delete` `net user /delete` `diskpart clean` `shred`, etc.
   - Bypass protection: `-EncodedCommand`, `Invoke-Expression`, `& $var`, Base64 execution,
     nested `cmd /c` recursive analysis
   - **CONFIRM** (asks for `y`): shutdown/restart, kill process, change permissions, uninstall,
     registry writes, etc.
   - **Write confirmation**: create/overwrite/append/move/rename/copy ask for confirmation
     (`FILE_WRITE_CONFIRM`, default on); `memory.txt` is managed by the memory tools directly
   - Non-interactive: dangerous commands are **denied** by default
   - **Self-destruction guard**: the project folder is off-limits — delete/clear/move/rename/
     overwrite of anything inside is BLOCKed, so the agent cannot break itself
2. **System prompt constraints**: deletion forbidden, writes require confirmation, no bypasses.
3. **Tool output truncation**: shell results are truncated to 2000 chars by default.
4. **Per-turn usage stats**: input/output tokens + cache hit (`SHOW_USAGE`, `--no-usage` to hide).

> Note: static analysis cannot be 100% robust against arbitrary obfuscation; avoid running on
> important machines without backups.

## Configuration: one config.json

All config lives in **`config.json`** at the project root (auto-generated on first run with a
commented template; an existing config.json is **never** overwritten — delete it manually to reset).
Supports `//` comments, editable in VS Code:

```jsonc
{
  // Model & API
  "DEFAULT_MODEL": "deepseek-v4-flash",   // or deepseek-v4-pro
  "OPENAI_API_KEY": "sk-xxx",             // required; or env var SGPT_API_KEY
  "API_BASE_URL": "https://api.deepseek.com",

  // Context & tokens (key to savings)
  "CONTEXT_TOKEN_BUDGET": 65536,          // history budget, oldest dropped beyond this
  "MAX_OUTPUT_TOKENS": 4096,              // per-answer cap (0 = unlimited)
  "TOOL_OUTPUT_MAX_CHARS": 2000,          // tool result truncation
  "SESSION_MAX_MESSAGES": 100,

  // Sampling
  "TEMPERATURE": 0.0,
  "TOP_P": 1.0,

  // Safety
  "CONFIRM_DANGEROUS": true,        // confirm dangerous commands (shutdown/kill/uninstall...)
  "FILE_WRITE_CONFIRM": true,       // confirm file writes (create/overwrite/move/rename/copy)

  // Tools & interaction
  "ENABLE_TOOLS": true,
  "STREAM": true,
  "SHOW_USAGE": true,               // show per-turn token usage and cache hits
  "REQUEST_TIMEOUT": 120,

  // Paths (empty = project folder defaults, moves with the folder)
  "SESSIONS_DIR": "",
  "MEMORY_FILE": "",

  // Custom instructions (appended to the system prompt; no code changes needed)
  "CUSTOM_INSTRUCTIONS": ""
}
```

- **Precedence**: env var > config.json > built-in default; env vars use `SGPT_`+key
  (e.g. `SGPT_CONTEXT_TOKEN_BUDGET`)
- **When it takes effect**: restart; CLI flags (`--model`/`--temperature`/`--max-output` etc.)
  override config for that run

## Where is the system prompt

- **Built-in prompt**: in `make_system_prompt()` in `pocketshell/api.py` (safety rules + current
  working directory + tool usage rules + Chinese answer style). Generated at runtime; the
  "current working directory" refreshes automatically.
- **Add your own rules**: no code changes — set `CUSTOM_INSTRUCTIONS` in `config.json`; the text
  is appended to the prompt as 【用户自定义指令】. The built-in safety rules always stay on top.

## Token-saving strategy

- **Automatic context management**: history is truncated to `CONTEXT_TOKEN_BUDGET`
  (oldest dropped, system + recent kept); `/clear` (or `--clear default`) for a fresh context;
  `/context` in REPL shows live token usage.
- Tool output truncated (2000 chars default).
- **DeepSeek reasoning content shown but never stored in history** — each turn's reasoning is
  billed once.
- Memory lives in `memory.txt`, recalled on demand, never resident in context.
- Only 7 lightweight tools — far smaller schema overhead than heavy agents.

## GREEN edition (bundled Python, ⚠️ Windows only)

`pocketshell-green.zip` bundles **Windows Python 3.13.15** (official embeddable package):
no Python install, no pip, no registry writes, no PATH changes. Launch with `green.bat`.

- **Size note**: ~10.6MB (zip) / ~40MB (extracted) — much larger than the portable edition (45KB)
  because it ships the whole Python interpreter. That's by design: size traded for zero-install.
  The portable edition needs only a system Python 3.10+; features are identical.
- **⚠️ Windows only**: the bundled Python is the Windows build. On Linux/macOS use the **portable
  edition** (source); Linux ships Python anyway.
- The bundled Python version is fixed; re-download the package to update it.

## Development & tests

```bash
cd pocketshell                 # repo root
HOME=$(mktemp -d) python3 -m pytest tests/ -q
```

## License

[Mozilla Public License Version 2.0](LICENSE)
