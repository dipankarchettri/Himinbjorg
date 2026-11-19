import json
from pathlib import Path

import pytest

from find_first_in_sorted import find_first_in_sorted

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "find_first_in_sorted.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_find_first_in_sorted(input_data, expected):
    assert find_first_in_sorted(*input_data) == expected
