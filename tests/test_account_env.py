import inspect

import pytest

from televibe.account import Account
from televibe.env import check_turn_env, child_env
from televibe.errors import TelevibeError


def test_account_requires_home(tmp_path):
    """REQ-ACCOUNT-1: home is required and has no default; None is refused."""
    assert inspect.signature(Account).parameters["home"].default is inspect.Parameter.empty
    with pytest.raises(TelevibeError):
        Account(None)
    assert Account(tmp_path / "a" / ".." / "a").home == (tmp_path / "a").resolve()


def test_account_home_expands_user(monkeypatch, tmp_path):
    """REQ-ACCOUNT-1: '~' in home is the caller's home, resolved."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Account("~/acc").home == (tmp_path / "acc").resolve()


def test_credentials_hidden_from_repr(tmp_path):
    """REQ-ACCOUNT-3: credentials never appear in repr()."""
    account = Account(tmp_path, credentials={"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"})
    assert "tok-123" not in repr(account)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in repr(account)
    assert account.credentials["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"
    with pytest.raises(TypeError):
        account.credentials["X"] = "y"  # read-only view


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"])
def test_api_keys_refused_in_credentials(tmp_path, name):
    """REQ-ENV-2: API keys are refused in credentials."""
    with pytest.raises(TelevibeError, match=name):
        Account(tmp_path, credentials={name: "x"})


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"])
def test_api_keys_refused_in_turn_env(name):
    """REQ-ENV-2: API keys are refused in a turn's env."""
    with pytest.raises(TelevibeError, match=name):
        check_turn_env({name: "x"})


@pytest.mark.parametrize("name", ["CLAUDE_CONFIG_DIR", "CODEX_HOME"])
def test_account_vars_refused_in_turn_env(name):
    """REQ-ENV-3: the account variable is chosen through Account, not a turn's env."""
    with pytest.raises(TelevibeError, match=name):
        check_turn_env({name: "/elsewhere"})


def test_turn_env_must_be_str_to_str():
    """REQ-API-2: a non-string env value is refused at the call."""
    with pytest.raises(TelevibeError):
        check_turn_env({"X": 1})


def test_child_env_is_an_allowlist(tmp_path):
    """REQ-ENV-1: only allowlisted names are inherited; account var, then credentials, then turn env on top."""
    parent = {
        "PATH": "/bin", "HOME": "/h", "USER": "u", "LOGNAME": "u", "SHELL": "/bin/sh", "LANG": "C",
        "TERM": "xterm", "TMPDIR": "/t", "TZ": "UTC", "LC_ALL": "C", "LC_CTYPE": "UTF-8",
        "AWS_SECRET_ACCESS_KEY": "leak", "CLAUDE_CONFIG_DIR": "/person/.claude", "ANTHROPIC_API_KEY": "leak",
    }
    account = Account(tmp_path, credentials={"TOKEN": "from-credentials", "SHARED": "credentials"})
    env = child_env("CLAUDE_CONFIG_DIR", account, {"SHARED": "turn", "EXTRA": "1"}, parent=parent)
    assert env == {
        "PATH": "/bin", "HOME": "/h", "USER": "u", "LOGNAME": "u", "SHELL": "/bin/sh", "LANG": "C",
        "TERM": "xterm", "TMPDIR": "/t", "TZ": "UTC", "LC_ALL": "C", "LC_CTYPE": "UTF-8",
        "CLAUDE_CONFIG_DIR": str(tmp_path.resolve()),
        "TOKEN": "from-credentials", "SHARED": "turn", "EXTRA": "1",
    }


@pytest.mark.parametrize("name", ["CLAUDE_CONFIG_DIR", "CODEX_HOME"])
def test_account_vars_refused_in_credentials(tmp_path, name):
    """REQ-ENV-3, REQ-ACCOUNT-2: credentials cannot point the agent at another account."""
    with pytest.raises(TelevibeError, match=name):
        Account(tmp_path, credentials={name: "/person/.claude"})


@pytest.mark.parametrize("env", [{"": "x"}, {"A=B": "x"}, {"A\0": "x"}, {"A": "x\0y"}])
def test_env_the_os_refuses_is_refused_at_the_call(tmp_path, env):
    """REQ-API-2: names and values the OS cannot pass to a process raise at the call."""
    with pytest.raises(TelevibeError):
        check_turn_env(env)
    with pytest.raises(TelevibeError):
        Account(tmp_path, credentials=env)
