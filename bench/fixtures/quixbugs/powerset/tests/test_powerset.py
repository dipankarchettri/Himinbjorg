import json
from pathlib import Path

import pytest

from powerset import powerset

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "powerset.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_powerset(input_data, expected):
    assert powerset(*input_data) == expected
