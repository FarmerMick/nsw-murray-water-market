import json
from pathlib import Path

import pytest

from murraywater import store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def mil_raw():
    return {name: json.loads((FIXTURES / f"mil_{name}.json").read_text(encoding="utf-8"))
            for name in ("summary", "dailysales", "exchangetables", "waterdata")}


@pytest.fixture
def con(tmp_path):
    connection = store.connect(tmp_path / "test.sqlite")
    yield connection
    connection.close()


class FakeClient:
    """Serves canned responses in place of PoliteClient and records what was asked for."""

    def __init__(self, responses):
        self.responses = responses  # callable(url, form) -> bytes
        self.calls = []
        self.requests_made = 0

    def fetch(self, url, form=None):
        self.calls.append((url, form))
        self.requests_made += 1
        return self.responses(url, form)


@pytest.fixture
def fake_client():
    return FakeClient
