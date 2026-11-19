import json
from pathlib import Path

import pytest

from quicksort import quicksort

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "quicksort.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_quicksort(input_data, expected):
    assert quicksort(*input_data) == expected
