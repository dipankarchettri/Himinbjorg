import json
from pathlib import Path

import pytest

from pascal import pascal

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "pascal.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_pascal(input_data, expected):
    assert pascal(*input_data) == expected
