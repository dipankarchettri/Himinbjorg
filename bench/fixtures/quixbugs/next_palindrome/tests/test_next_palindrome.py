import json
from pathlib import Path

import pytest

from next_palindrome import next_palindrome

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "next_palindrome.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_next_palindrome(input_data, expected):
    assert next_palindrome(*input_data) == expected
