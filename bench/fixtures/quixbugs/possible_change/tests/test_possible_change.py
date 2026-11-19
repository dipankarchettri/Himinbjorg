import json
from pathlib import Path

import pytest

from possible_change import possible_change

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "possible_change.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_possible_change(input_data, expected):
    assert possible_change(*input_data) == expected
