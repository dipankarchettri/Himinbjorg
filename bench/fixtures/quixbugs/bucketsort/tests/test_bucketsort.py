import json
from pathlib import Path

import pytest

from bucketsort import bucketsort

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "bucketsort.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_bucketsort(input_data, expected):
    assert bucketsort(*input_data) == expected
