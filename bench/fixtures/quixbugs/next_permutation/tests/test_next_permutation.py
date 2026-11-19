import json
from pathlib import Path

import pytest

from next_permutation import next_permutation

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "next_permutation.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_next_permutation(input_data, expected):
    assert next_permutation(*input_data) == expected
