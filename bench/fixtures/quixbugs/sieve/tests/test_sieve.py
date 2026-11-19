import json
from pathlib import Path

import pytest

from sieve import sieve

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "sieve.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_sieve(input_data, expected):
    assert sieve(*input_data) == expected
