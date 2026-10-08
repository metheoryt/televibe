"""The child process environment (REQ-ENV-1..3)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from televibe.errors import TelevibeError

if TYPE_CHECKING:
    from televibe.account import Account

INHERITED = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ"})
API_KEYS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"})
ACCOUNT_VARS = frozenset({"CLAUDE_CONFIG_DIR", "CODEX_HOME"})


def _check_str_map(env: Mapping[str, str], where: str) -> dict[str, str]:
    if not isinstance(env, Mapping):
        raise TelevibeError(f"{where} must be a mapping of str to str")
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TelevibeError(f"{where} must map str to str; {key!r} does not")
    return dict(env)


def refuse_api_keys(names: Iterable[str], where: str) -> None:
    found = sorted(API_KEYS.intersection(names))
    if found:
        raise TelevibeError(
            f"{', '.join(found)} refused in {where}: televibe v1 runs agents on subscriptions only, "
            "and an API key would bill the API"
        )


def check_credentials(credentials: Mapping[str, str]) -> dict[str, str]:
    checked = _check_str_map(credentials, "credentials")
    refuse_api_keys(checked, "credentials")
    return checked


def check_turn_env(env: Mapping[str, str]) -> dict[str, str]:
    checked = _check_str_map(env, "a turn's env")
    refuse_api_keys(checked, "a turn's env")
    found = sorted(ACCOUNT_VARS.intersection(checked))
    if found:
        raise TelevibeError(f"{', '.join(found)} refused in a turn's env: choose the account through Account")
    return checked


def child_env(
    account_var: str,
    account: Account,
    turn_env: Mapping[str, str],
    parent: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    env = {name: value for name, value in parent.items() if name in INHERITED or name.startswith("LC_")}
    env[account_var] = str(account.home)
    env.update(account.credentials)
    env.update(turn_env)
    return env
