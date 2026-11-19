import json
from pathlib import Path

import pytest

from kth import kth

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "kth.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_kth(input_data, expected):
    assert kth(*input_data) == expected
