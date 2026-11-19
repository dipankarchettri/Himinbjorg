import json
from pathlib import Path

import pytest

from rpn_eval import rpn_eval

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "rpn_eval.jsonl").read_text().splitlines() if line.strip()]

@pytest.mark.parametrize("input_data,expected", CASES)
def test_rpn_eval(input_data, expected):
    assert rpn_eval(*input_data) == expected
