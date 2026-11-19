import json
from pathlib import Path

import pytest

from to_base import to_base

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "to_base.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_to_base(input_data, expected):
    assert to_base(*input_data) == expected
