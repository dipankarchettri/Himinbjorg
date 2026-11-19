import json
from pathlib import Path

import pytest

from sqrt import sqrt

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "sqrt.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_sqrt(input_data, expected):
    assert sqrt(*input_data) == pytest.approx(expected, abs=input_data[-1])
