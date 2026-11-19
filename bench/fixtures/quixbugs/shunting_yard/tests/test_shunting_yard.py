import json
from pathlib import Path

import pytest

from shunting_yard import shunting_yard

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "shunting_yard.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_shunting_yard(input_data, expected):
    assert shunting_yard(*input_data) == expected
