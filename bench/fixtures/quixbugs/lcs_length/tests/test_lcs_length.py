import json
from pathlib import Path

import pytest

from lcs_length import lcs_length

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "lcs_length.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_lcs_length(input_data, expected):
    assert lcs_length(*input_data) == expected
