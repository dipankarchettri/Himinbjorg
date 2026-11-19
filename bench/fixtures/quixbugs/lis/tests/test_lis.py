import json
from pathlib import Path

import pytest

from lis import lis

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "lis.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_lis(input_data, expected):
    assert lis(*input_data) == expected
