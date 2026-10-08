import dataclasses
import json

import pytest

from televibe.access import Access
from televibe.account import Account
from televibe.errors import TelevibeError
from televibe.providers.base import Provider
from televibe.session import reported


class Fixed(Provider):
    kind = "fixed"
    binary = "fixed"
    account_var = "FIXED_HOME"
    access_levels = frozenset({Access.READ_ONLY})
    fixed_ids = True

    def argv(self, session, options):
        return [self.binary]

    def parser(self, session):
        raise NotImplementedError


class Named(Fixed):
    kind = "named"
    fixed_ids = False


def test_session_fields(tmp_path, monkeypatch):
    """REQ-SESSION-1: provider kind, account home, absolute cwd, id, started; immutable."""
    monkeypatch.chdir(tmp_path)
    provider = Fixed(Account(tmp_path / "acc"))
    session = provider.new_session("work")
    assert session.provider == "fixed"
    assert session.home == (tmp_path / "acc").resolve()
    assert session.cwd == (tmp_path / "work").resolve()
    assert session.id is None and session.started is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.id = "x"


def test_resume_session_is_started(tmp_path):
    """REQ-SESSION-2: resume_session names a session the agent already has."""
    session = Fixed(Account(tmp_path)).resume_session(tmp_path, "abc")
    assert session.id == "abc" and session.started is True
    with pytest.raises(TelevibeError):
        Fixed(Account(tmp_path)).resume_session(tmp_path, "")


def test_load_session_checks_kind_and_home(tmp_path):
    """REQ-SESSION-2: load_session raises on another provider kind or another account home."""
    session = Fixed(Account(tmp_path / "a")).resume_session(tmp_path, "abc")
    with pytest.raises(TelevibeError, match="named"):
        Named(Account(tmp_path / "a")).load_session(session.dump())
    with pytest.raises(TelevibeError, match="home"):
        Fixed(Account(tmp_path / "b")).load_session(session.dump())


@pytest.mark.parametrize("text", ["not json", "[]", "{}", '{"provider": "fixed"}'])
def test_load_session_rejects_garbage(tmp_path, text):
    """REQ-SESSION-2: a stored string that is not a session dump raises."""
    with pytest.raises(TelevibeError):
        Fixed(Account(tmp_path)).load_session(text)


def test_dump_round_trips_without_credentials(tmp_path):
    """REQ-SESSION-3: dump() is JSON that load_session turns back into an equal session."""
    provider = Fixed(Account(tmp_path, credentials={"TOKEN": "s3cret"}))
    for session in (
        provider.new_session(tmp_path),
        provider.new_session(tmp_path, id="fixed-1"),
        provider.resume_session(tmp_path, "abc"),
        reported(provider.new_session(tmp_path), "from-agent"),
    ):
        text = session.dump()
        assert json.loads(text).keys() == {"provider", "home", "cwd", "id", "started"}
        assert "s3cret" not in text and "TOKEN" not in text
        assert provider.load_session(text) == session


def test_fixed_id_only_where_supported(tmp_path):
    """REQ-SESSION-5: a fixed id is accepted only by a provider that lets the caller choose it."""
    assert Fixed(Account(tmp_path)).new_session(tmp_path, id="mine").id == "mine"
    with pytest.raises(TelevibeError):
        Named(Account(tmp_path)).new_session(tmp_path, id="mine")


def test_reported_sets_id_and_started(tmp_path):
    """REQ-SESSION-2: the session in Started/Done/Failed has started set."""
    session = reported(Fixed(Account(tmp_path)).new_session(tmp_path), "abc")
    assert session.id == "abc" and session.started is True


def test_provider_needs_an_account(tmp_path):
    """REQ-API-2: a provider without an Account raises at the call."""
    with pytest.raises(TelevibeError):
        Fixed(tmp_path)


def test_check_access(tmp_path):
    """REQ-ACCESS-1: a level the provider does not declare raises."""
    provider = Fixed(Account(tmp_path))
    provider.check_access(Access.READ_ONLY)
    with pytest.raises(TelevibeError, match="FULL"):
        provider.check_access(Access.FULL)
