import json
from pathlib import Path

import pytest

from kheapsort import kheapsort

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "kheapsort.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_kheapsort(input_data, expected):
    assert list(kheapsort(*input_data)) == expected
