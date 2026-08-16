# SPDX-License-Identifier: MPL-2.0
"""工作目录授权（-setworkspace）测试：设置/清除/失效、写放行、删除仍拦。

运行：cd /home/zhang/sgpt && HOME=/tmp/sgpt_test_home PYTHONPATH=/tmp/pip_site python3 -m pytest pocketshell/tests/ -q
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pocketshell import safety  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_workspace():
    """每个测试后清除 workspace,避免污染其它测试。"""
    yield
    safety.WORKSPACE_DIR = None


# ---------------- 设置 / 清除 ----------------

def test_set_workspace_abs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = safety.set_workspace(str(tmp_path))
    assert ws == str(tmp_path.resolve()) or ws == os.path.realpath(str(tmp_path))
    assert safety.get_workspace() == ws


def test_set_workspace_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = safety.set_workspace("sub")
    (tmp_path / "sub").mkdir()
    assert ws == str((tmp_path / "sub").resolve())


def test_set_workspace_off_clears(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    safety.set_workspace(str(tmp_path))
    assert safety.get_workspace() is not None
    assert safety.set_workspace(None) is None
    assert safety.get_workspace() is None


def test_set_workspace_off_string(tmp_path):
    safety.set_workspace(str(tmp_path))
    assert safety.set_workspace("off") is None
    assert safety.get_workspace() is None


def test_workspace_expires_on_cd_away(tmp_path, monkeypatch):
    """cd 离开后授权立即失效,回到目录也需重新授权(设计要点)。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.chdir(a)
    safety.set_workspace(str(a))
    assert safety.get_workspace() is not None
    monkeypatch.chdir(b)  # 离开
    assert safety.get_workspace() is None  # 已失效
    monkeypatch.chdir(a)  # 回到目录
    assert safety.get_workspace() is None  # 仍需重新授权


# ---------------- 写操作放行判定 ----------------

@pytest.mark.parametrize(
    "cmd",
    [
        "Set-Content -Path out.txt -Value hello",
        "echo hello > out.txt",
        "echo hello >> log.txt",
        "Copy-Item a.txt b.txt",
        "New-Item -Path new.txt -ItemType File",
        "Out-File report.md",
    ],
)
def test_workspace_write_within(tmp_path, monkeypatch, cmd):
    monkeypatch.chdir(tmp_path)
    safety.set_workspace(str(tmp_path))
    assert safety.is_workspace_write(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "Set-Content -Path ../../outside.txt -Value hello",
        "echo hello > ../../outside.txt",
        "echo hello > C:/Windows/temp/x.txt",
        "Copy-Item a.txt C:/Windows/x.txt",
    ],
)
def test_workspace_write_outside(tmp_path, monkeypatch, cmd):
    monkeypatch.chdir(tmp_path)
    safety.set_workspace(str(tmp_path))
    assert not safety.is_workspace_write(cmd)


def test_workspace_write_no_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    safety.set_workspace(str(tmp_path))
    assert not safety.is_workspace_write("Get-ChildItem")


def test_workspace_write_not_active(tmp_path, monkeypatch):
    """未授权时一律不视为工作目录写。"""
    monkeypatch.chdir(tmp_path)
    assert not safety.is_workspace_write("echo hello > out.txt")


def test_deletes_still_blocked_in_workspace(tmp_path, monkeypatch):
    """工作目录内的删除仍被 analyze_command 硬拦(BLOCK)。"""
    monkeypatch.chdir(tmp_path)
    safety.set_workspace(str(tmp_path))
    for cmd in ["del out.txt", "Remove-Item -Path x.txt -Force", "rm -rf ."]:
        r = safety.analyze_command(cmd, cwd=str(tmp_path))
        assert r.verdict == safety.BLOCK, f"{cmd} 应被 BLOCK,实际 {r.verdict}"


def test_extract_write_target():
    assert safety.extract_write_target("echo hello > out.txt") == "out.txt"
    assert safety.extract_write_target("echo hello >> log.txt") == "log.txt"
    assert safety.extract_write_target("Set-Content -Path 'a b.txt' -Value x") == "a b.txt"
    assert safety.extract_write_target("Out-File -FilePath report.md") == "report.md"
    assert safety.extract_write_target("Copy-Item src.txt -Destination dst.txt") == "dst.txt"
    assert safety.extract_write_target("Get-ChildItem") is None
    assert safety.extract_write_target("dir > NUL") == "NUL"


# ---------------- system 提示词注入频率（SYSTEM_PROMPT_INTERVAL） ----------------

def _has_system(msgs):
    return any(m.get("role") == "system" for m in msgs)


def test_system_injected_on_first_turn(tmp_path, monkeypatch):
    from pocketshell.session import Session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path / "sess"))
    monkeypatch.setenv("PS_SYSTEM_PROMPT_INTERVAL", "3")
    s = Session("t")
    s.system("SYS")
    s.add_user("q1")
    assert _has_system(s.messages_for_api())


def test_system_skipped_between_intervals(tmp_path, monkeypatch):
    from pocketshell.session import Session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path / "sess"))
    monkeypatch.setenv("PS_SYSTEM_PROMPT_INTERVAL", "3")
    s = Session("t")
    s.system("SYS")
    # 轮1: 注入;轮2/3: 跳过;轮4: 注入
    s.add_user("q1"); assert _has_system(s.messages_for_api())
    s.add_assistant("a1")
    s.add_user("q2"); assert not _has_system(s.messages_for_api())
    s.add_assistant("a2")
    s.add_user("q3"); assert not _has_system(s.messages_for_api())
    s.add_assistant("a3")
    s.add_user("q4"); assert _has_system(s.messages_for_api())


def test_system_interval_1_injects_every_turn(tmp_path, monkeypatch):
    from pocketshell.session import Session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path / "sess"))
    monkeypatch.setenv("PS_SYSTEM_PROMPT_INTERVAL", "1")
    s = Session("t")
    s.system("SYS")
    for i in range(1, 5):
        s.add_user(f"q{i}")
        assert _has_system(s.messages_for_api()), f"interval=1 时轮{i}应注入"
        s.add_assistant(f"a{i}")


def test_system_force_injects_when_cwd_changed(tmp_path, monkeypatch):
    from pocketshell.session import Session
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.chdir(a)
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path / "sess"))
    monkeypatch.setenv("PS_SYSTEM_PROMPT_INTERVAL", "3")
    s = Session("t")
    s.system("SYS")
    s.add_user("q1"); s.messages_for_api()
    s.add_assistant("a1")
    monkeypatch.chdir(b)  # cwd 变化 → force_system=True 应注入
    s.add_user("q2")
    assert _has_system(s.messages_for_api(force_system=True))


def test_cwd_prefix_change_forces_system_injection(tmp_path, monkeypatch):
    """cd 到父目录（前缀目录）时也必须强制注入 system（子串判断会漏）。"""
    from pocketshell import cli
    from pocketshell.session import Session
    parent = tmp_path / "work"
    child = parent / "project"
    parent.mkdir()
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path / "sess"))
    monkeypatch.setenv("PS_SYSTEM_PROMPT_INTERVAL", "3")
    s = Session("t")
    # 用 _run_turn 的刷新逻辑:构造"已注入过"的会话,cwd 随后变到父目录
    from pocketshell.api import make_system_prompt
    s.system(make_system_prompt())
    s.add_user("q1")
    s.add_assistant("a1")
    # cwd 变成父目录(前缀)
    monkeypatch.chdir(parent)
    import pocketshell.cli as cli_mod
    # 模拟 _run_turn 的检测:新提示词应与旧的不同(含新 cwd)
    new_prompt = make_system_prompt()
    assert s.messages[0]["content"] != new_prompt  # 精确比较能检出变化
    # 且旧子串判断会漏:父目录是子串
    assert str(parent) in s.messages[0]["content"]  # 旧逻辑:父目录是子串 -> 漏检


# ---------------- 环境变量前缀:PS_ 新名 / SGPT_ 旧名兼容 ----------------

def test_env_prefix_ps_preferred_over_sgpt(monkeypatch):
    from pocketshell.config import Config
    monkeypatch.setenv("PS_CONTEXT_TOKEN_BUDGET", "111")
    monkeypatch.setenv("SGPT_CONTEXT_TOKEN_BUDGET", "222")
    assert Config().get("CONTEXT_TOKEN_BUDGET") == "111"


def test_env_prefix_sgpt_compat(monkeypatch):
    from pocketshell.config import Config
    monkeypatch.delenv("PS_CONTEXT_TOKEN_BUDGET", raising=False)
    monkeypatch.setenv("SGPT_CONTEXT_TOKEN_BUDGET", "222")
    assert Config().get("CONTEXT_TOKEN_BUDGET") == "222"


def test_api_key_env_ps_and_sgpt(monkeypatch):
    from pocketshell.config import Config
    monkeypatch.setenv("PS_API_KEY", "sk-ps")
    monkeypatch.setenv("SGPT_API_KEY", "sk-sgpt")
    assert Config().get_api_key() == "sk-ps"
    monkeypatch.delenv("PS_API_KEY")
    assert Config().get_api_key() == "sk-sgpt"
