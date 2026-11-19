import json
from pathlib import Path

import pytest

from gcd import gcd

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "gcd.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_gcd(input_data, expected):
    assert gcd(*input_data) == expected
