"""Pytest configuration for last30days-cn.

Adapted from the upstream ``tests/conftest.py`` (which only inserts the skill's
``scripts`` directory on ``sys.path`` so ``from lib import ...`` resolves). This
port adds:

- a ``LAST30DAYS_CONFIG_DIR=""`` default so the suite runs in *clean mode*
  (no ``.env`` / Keychain / browser-cookie bleed-through). Set it before any
  ``lib.env`` import because ``env`` reads the variable at module-import time.
- a ``fixture_json`` helper to load the Chinese sample payloads under
  ``tests/fixtures/`` for the per-provider ``parse_*`` tests.
"""

import json
import os
import sys
from pathlib import Path

# Run in clean/no-config mode unless a test explicitly overrides it. Must be set
# before lib.env is imported (env binds CONFIG_DIR/CONFIG_FILE at import time
# from this variable). os.environ.setdefault keeps an externally-provided value.
os.environ.setdefault("LAST30DAYS_CONFIG_DIR", "")

_SKILL_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "last30days-cn"
    / "scripts"
)
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

import pytest  # noqa: E402  (path setup must precede imports of the package)


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the tests/fixtures directory."""
    return _FIXTURES


def load_fixture(name: str):
    """Load and parse a JSON fixture by filename (e.g. ``"weibo.json"``)."""
    with open(_FIXTURES / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixture_json():
    """Return the ``load_fixture`` callable for use inside tests."""
    return load_fixture
