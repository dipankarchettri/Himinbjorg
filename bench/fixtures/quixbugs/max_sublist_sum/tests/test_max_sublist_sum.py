import json
from pathlib import Path

import pytest

from max_sublist_sum import max_sublist_sum

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "max_sublist_sum.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_max_sublist_sum(input_data, expected):
    assert max_sublist_sum(*input_data) == expected
