import json
from pathlib import Path

import pytest

from is_valid_parenthesization import is_valid_parenthesization

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "is_valid_parenthesization.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_is_valid_parenthesization(input_data, expected):
    assert is_valid_parenthesization(*input_data) == expected
