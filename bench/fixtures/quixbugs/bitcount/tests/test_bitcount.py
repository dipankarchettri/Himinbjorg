import json
from pathlib import Path

import pytest

from bitcount import bitcount

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "bitcount.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_bitcount(input_data, expected):
    assert bitcount(*input_data) == expected
