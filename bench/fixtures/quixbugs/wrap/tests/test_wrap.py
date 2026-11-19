import json
from pathlib import Path

import pytest

from wrap import wrap

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "wrap.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_wrap(input_data, expected):
    assert wrap(*input_data) == expected
