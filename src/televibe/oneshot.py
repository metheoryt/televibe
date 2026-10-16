"""One-shot calls: an answer in a JSON schema, with no session, no tools and no place in the queue (section 12)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from televibe.env import check_turn_env
from televibe.errors import TelevibeError
from televibe.events import FailReason, Limits

DEFAULT_TIMEOUT_S = 180.0


@dataclass(frozen=True, slots=True)
class Answer:
    """What a one-shot call returned (REQ-ASK-1)."""

    data: Any
    usage: dict[str, Any]
    limits: Limits | None


class AskFailed(TelevibeError):
    """A one-shot call that did not return an answer (REQ-ASK-4)."""

    def __init__(self, reason: FailReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AskSpec:
    prompt: str
    schema_json: str
    system: str | None
    model: str | None
    env: dict[str, str] = field(repr=False)  # may hold tokens (REQ-ENV-4)
    timeout_s: float = DEFAULT_TIMEOUT_S


def check_ask_args(
    prompt: str,
    schema: Mapping[str, Any],
    system: str | None,
    model: str | None,
    env: Mapping[str, str],
    timeout_s: float,
) -> AskSpec:
    """Every misuse raises TelevibeError before a process starts (REQ-ASK-5)."""
    if not isinstance(prompt, str) or not prompt:
        raise TelevibeError("prompt must be a non-empty str")
    try:
        prompt.encode()
    except UnicodeEncodeError:
        raise TelevibeError("prompt must be text that encodes to UTF-8 (it holds a lone surrogate)") from None
    if not isinstance(schema, Mapping):
        raise TelevibeError("schema must be a mapping holding a JSON Schema")
    try:
        schema_json = json.dumps(dict(schema), separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TelevibeError(f"schema must be JSON-serializable: {exc}") from None
    for name, value in (("system", system), ("model", model)):
        if value is not None and not isinstance(value, str):
            raise TelevibeError(f"{name} must be a str or None")
        if value is not None and "\0" in value:
            raise TelevibeError(f"{name} must not contain a NUL character")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise TelevibeError("timeout_s must be a positive, finite number of seconds")
    return AskSpec(prompt, schema_json, system, model, check_turn_env(env), float(timeout_s))
