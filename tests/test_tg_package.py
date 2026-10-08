import subprocess
import sys


def _python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_core_does_not_import_the_layer():
    """REQ-TGPKG-2: `import televibe` imports neither televibe.telegram nor aiogram."""
    result = _python(
        "import sys, televibe; "
        "print(sorted(m for m in sys.modules if m == 'aiogram' or m.startswith(('aiogram.', 'televibe.telegram'))))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_layer_without_aiogram_names_the_extra():
    """REQ-TGPKG-3: without aiogram, importing televibe.telegram raises ImportError naming the extra."""
    result = _python("import sys; sys.modules['aiogram'] = None; import televibe.telegram")
    assert result.returncode != 0
    assert "ImportError" in result.stderr
    assert "televibe[telegram]" in result.stderr


def test_layer_imports_with_aiogram():
    """REQ-TGPKG-3: with aiogram installed, the layer imports."""
    result = _python("import televibe.telegram")
    assert result.returncode == 0, result.stderr
