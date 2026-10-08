import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
LOCAL = ("/Users/", "/home/", "/private/", "/var/folders/")


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.glob("*/*") if p.suffix in (".jsonl", ".stderr"))


def test_fixtures_exist_for_both_providers():
    """REQ-TEST-3: recorded outputs live in tests/fixtures/<provider>/."""
    assert {p.parent.name for p in _fixture_files()} == {"claude", "codex"}


def test_fixtures_carry_no_local_paths_or_ids():
    """REQ-TEST-3: local paths and ids are replaced with placeholders."""
    for path in _fixture_files():
        text = path.read_text()
        assert not [marker for marker in LOCAL if marker in text], path
        assert set(UUID.findall(text)) <= {"00000000-0000-0000-0000-000000000000"}, path


def test_every_fixture_names_its_cli_version():
    """REQ-TEST-3: each fixture is listed with the CLI version it was recorded with."""
    readme = (FIXTURES / "README.md").read_text()
    for path in _fixture_files():
        row = next((line for line in readme.splitlines() if f"`{path.parent.name}/{path.name}`" in line), "")
        assert re.search(r"\d+\.\d+\.\d+", row), f"{path} has no versioned row in tests/fixtures/README.md"
