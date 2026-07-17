from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lore"


@pytest.fixture
def fixture_text():
    """Read a checked-in lore fixture by filename."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def fixture_bytes():
    """Read a checked-in lore fixture by filename, as raw bytes."""

    def _read(name: str) -> bytes:
        return (FIXTURE_DIR / name).read_bytes()

    return _read
