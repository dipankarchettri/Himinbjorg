import json
from pathlib import Path

import pytest

from get_factors import get_factors

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "get_factors.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_get_factors(input_data, expected):
    assert get_factors(*input_data) == expected
