import json
from pathlib import Path

import pytest

from longest_common_subsequence import longest_common_subsequence

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "longest_common_subsequence.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_longest_common_subsequence(input_data, expected):
    assert longest_common_subsequence(*input_data) == expected
