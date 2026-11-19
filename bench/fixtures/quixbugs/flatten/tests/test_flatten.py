import json
from pathlib import Path

import pytest

from flatten import flatten

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "flatten.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_flatten(input_data, expected):
    assert list(flatten(*input_data)) == expected
