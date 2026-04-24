from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from main import app  # noqa: E402


def pytest_configure() -> None:
    fixtures_root = ROOT / "eval" / "fixtures"
    (fixtures_root / "meeting_exports").mkdir(parents=True, exist_ok=True)
    (fixtures_root / "twinmind_txt").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
