import json
from pathlib import Path

import pytest

from beeja.ingest import load_discussion

FIXTURES = Path(__file__).parent / "fixtures"


class ScriptedProvider:
    """Deterministic test double: returns pre-written responses in call order.

    Test-only. It lives in tests/ so the product cannot accidentally ship it.
    """

    name = "scripted:test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, system, prompt, schema):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        r = self.responses.pop(0)
        return r(prompt) if callable(r) else r


@pytest.fixture
def scripted():
    return ScriptedProvider


@pytest.fixture
def tabs_discussion():
    return load_discussion(FIXTURES / "tabs_vs_spaces.txt")


def load_json(name):
    return json.loads((FIXTURES / name).read_text())
