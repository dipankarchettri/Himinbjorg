import json
from pathlib import Path

import pytest

from mergesort import mergesort

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "mergesort.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_mergesort(input_data, expected):
    assert mergesort(*input_data) == expected
