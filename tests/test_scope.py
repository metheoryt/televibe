import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_CLIENTS = {"aiogram", "telegram", "slack", "slack_sdk", "discord", "telebot", "pyrogram", "telethon"}


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_chat_client_imports():
    """REQ-SCOPE-1: no module under src/ imports a chat client."""
    offenders = {
        str(path.relative_to(ROOT)): sorted(_imported_roots(path) & CHAT_CLIENTS)
        for path in (ROOT / "src").rglob("*.py")
        if _imported_roots(path) & CHAT_CLIENTS
    }
    assert offenders == {}


def test_no_runtime_dependencies():
    """REQ-SCOPE-2: pyproject.toml declares no runtime dependencies."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []


def test_python_floor_and_windows_refusal():
    """REQ-SCOPE-3: Python 3.12+, and importing on Windows raises a clear error."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["requires-python"] == ">=3.12"
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.platform = 'win32'; import televibe"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Linux and macOS only" in result.stderr
