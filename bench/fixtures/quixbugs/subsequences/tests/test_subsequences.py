import json
from pathlib import Path

import pytest

from subsequences import subsequences

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "subsequences.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_subsequences(input_data, expected):
    assert subsequences(*input_data) == expected
