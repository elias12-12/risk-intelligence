"""Configuration, read from the environment with dev-friendly defaults."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = REPO_ROOT / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
SEEDS_DIR = DB_DIR / "seeds"
VIEWS_DIR = DB_DIR / "views"
ACCEPTANCE_DIR = DB_DIR / "acceptance"
FIXTURES_DIR = REPO_ROOT / "fixtures"
CONTRACT_DIR = REPO_ROOT / "contract"

DEFAULT_DSN = "postgresql://glassbox:glassbox@localhost:55432/glassbox"
DEFAULT_TEST_DSN = "postgresql://glassbox:glassbox@localhost:55432/glassbox_test"
DEFAULT_SERVE_HOST = "127.0.0.1"


def _load_dotenv() -> None:
    """Minimal .env loader — avoids a dependency for four variables."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def dsn() -> str:
    return os.environ.get("GLASSBOX_DSN", DEFAULT_DSN)


def test_dsn() -> str:
    return os.environ.get("GLASSBOX_TEST_DSN", DEFAULT_TEST_DSN)


def serve_host() -> str:
    """The interface `python -m glassbox serve` binds to.

    Loopback by default, and that default is a position rather than an
    oversight: this service commits to whatever `GLASSBOX_DSN` points at, runs a
    background cycle on an interval, and answers `POST /authorize`, which can
    decline a charge. It does not listen beyond this machine unless somebody
    says so in an environment variable.

    The reason to say so is the console container. `docker-compose.yml` runs the
    console with no Python in it, so its dev proxy reaches this process over the
    Docker bridge — and a socket bound to 127.0.0.1 refuses exactly that, with a
    connection error that reads like the service is down when it is running
    fine. `GLASSBOX_HOST=0.0.0.0` is the pairing, and .env.example carries it.
    """
    return os.environ.get("GLASSBOX_HOST", DEFAULT_SERVE_HOST)


def reference_now() -> datetime:
    """The generator's reference instant. Fixtures are pinned to it."""
    raw = os.environ.get("GLASSBOX_NOW", "2026-01-15T15:00:00+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
