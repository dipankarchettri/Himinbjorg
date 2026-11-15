"""apply_patch regression tests. Every case is a failure taken from a
benchmark trace (2026-11-15), where the model retried the same broken edit
until the turn cap."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.write import apply_patch                     # noqa: E402
from bench.tasks import TASKS_BY_ID                           # noqa: E402

CALC = TASKS_BY_ID["zero_check"].files["calc.py"]


def patch(tmp_path, find, replace, body=CALC, **kw):
    f = tmp_path / "calc.py"
    f.write_text(body)
    out = apply_patch(str(f), find, replace, tmp_path, **kw)
    return out, f.read_text()


def divide_of(text):
    return text[text.index("def divide"):]


@pytest.mark.parametrize("replace", [
    "if b == 0:\\n            return None\\n        return a / b",
    "    if b == 0:\\n        return None\\n    return a / b",
    "if b == 0:\n    return None\nreturn a / b",
])
def test_midline_find_with_multiline_replace(tmp_path, replace):
    out, text = patch(tmp_path, "return a / b", replace)
    assert out.startswith("patched"), out
    assert "    if b == 0:\n        return None\n    return a / b" in divide_of(text)


def test_find_missing_comment_line(tmp_path):
    out, text = patch(tmp_path, "def divide(a, b):\n    return a / b",
                      "def divide(a, b):\n    if b == 0:\n        return None\n    return a / b")
    assert out.startswith("patched"), out
    assert "    if b == 0:\n        return None\n    return a / b" in text


def test_append_keeps_comment_the_model_omitted(tmp_path):
    out, text = patch(tmp_path, "def divide(a, b):\n    return a / b\n",
                      "def divide(a, b):\n    return a / b\n\ndef power(a, b):\n    return a ** b\n")
    assert out.startswith("patched"), out
    assert "# BUG" in text and "def power(a, b):\n    return a ** b" in text


def test_line_numbers_copied_from_read_file(tmp_path):
    out, text = patch(tmp_path, "   18      return a / b",
                      "   18      return a / b if b else None")
    assert out.startswith("patched"), out
    assert "return a / b if b else None" in text


def test_ambiguous_find_suggests_replace_all(tmp_path):
    body = "def f():\n    return multiply(1, 2) + multiply(3, 4)\n"
    out, text = patch(tmp_path, "multiply(", "times(", body=body)
    assert out.startswith("ERROR") and "replace_all" in out
    out, text = patch(tmp_path, "multiply(", "times(", body=body, replace_all=True)
    assert out.startswith("patched") and "multiply" not in text


def test_already_applied_edit_is_reported(tmp_path):
    body = "from calc import times\n"
    out, text = patch(tmp_path, "from calc import multiply", "from calc import times", body=body)
    assert out.startswith("no change") and text == body


def test_unparseable_result_still_refused(tmp_path):
    out, text = patch(tmp_path, "    return a / b", "    if b == 0\n        return None")
    assert out.startswith("ERROR") and text == CALC


def test_not_found_shows_verbatim_region(tmp_path):
    out, _ = patch(tmp_path, "def divide(a, b):\n    retrun a // b", "x")
    assert out.startswith("ERROR") and "# BUG: no zero check" in out


def test_empty_find_appends(tmp_path):
    out, text = patch(tmp_path, "", "\n\ndef power(a, b):\n    return a ** b\n")
    assert out.startswith("patched"), out
    assert text.startswith(CALC.rstrip("\n")) and text.endswith("def power(a, b):\n    return a ** b\n")


# ------------------------------------------------------------ rename_symbol

from agent.tools.write import PREVIEW_PREFIX, rename_symbol      # noqa: E402
from bench.tasks import materialise                              # noqa: E402


def test_rename_symbol_across_files(tmp_path):
    repo = materialise(TASKS_BY_ID["rename_symbol"], tmp_path / "r")
    out = rename_symbol("multiply", "times", repo)
    assert out.startswith("renamed multiply -> times"), out
    assert TASKS_BY_ID["rename_symbol"].verify(repo)


def test_rename_symbol_whole_word_only(tmp_path):
    (tmp_path / "a.py").write_text("def multiply(a, b):\n    return a * b\n\nmultiply_all = 1\n")
    rename_symbol("multiply", "times", tmp_path)
    text = (tmp_path / "a.py").read_text()
    assert "def times(" in text and "multiply_all = 1" in text


def test_rename_symbol_dry_run_writes_nothing(tmp_path):
    (tmp_path / "a.py").write_text("x = old_name\n")
    out = rename_symbol("old_name", "new_name", tmp_path, dry_run=True)
    assert out.startswith(PREVIEW_PREFIX) and "+x = new_name" in out
    assert (tmp_path / "a.py").read_text() == "x = old_name\n"


def test_rename_symbol_refuses_keywords_and_is_all_or_nothing(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    (tmp_path / "b.py").write_text("print(value)\n")
    assert rename_symbol("value", "class", tmp_path).startswith("ERROR")
    assert (tmp_path / "a.py").read_text() == "value = 1\n"
    assert rename_symbol("value", "not an ident", tmp_path).startswith("ERROR")


def test_rename_symbol_skips_secret_files(tmp_path):
    (tmp_path / ".env").write_text("API_TOKEN=abc\n")
    (tmp_path / "a.py").write_text("API_TOKEN = None\n")
    rename_symbol("API_TOKEN", "TOKEN", tmp_path)
    assert (tmp_path / ".env").read_text() == "API_TOKEN=abc\n"


def test_rename_symbol_already_done(tmp_path):
    (tmp_path / "a.py").write_text("def times(a, b):\n    return a * b\n")
    assert rename_symbol("multiply", "times", tmp_path).startswith("no change")


def test_not_found_points_at_the_file_that_has_it(tmp_path):
    repo = materialise(TASKS_BY_ID["shop_bulk_bug"], tmp_path / "r")
    out = apply_patch(str(repo / "shop/pricing.py"), "if quantity > config.BULK_THRESHOLD:",
                      "if quantity >= config.BULK_THRESHOLD:", repo)
    assert out.startswith("ERROR") and "shop/discounts.py line" in out
    assert "quantity > config" in (repo / "shop/discounts.py").read_text()   # untouched


def test_read_past_the_end_returns_the_small_file(tmp_path):
    from agent.tools.read import read_file
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    out = read_file(str(tmp_path / "a.py"), tmp_path, start=37, end=45)
    assert "only 2 lines" in out and "x = 1" in out


def test_not_found_anywhere_names_where_identifiers_live(tmp_path):
    repo = materialise(TASKS_BY_ID["shop_bulk_bug"], tmp_path / "r")
    out = apply_patch(str(repo / "shop/orders.py"),
                      "if quantity >= threshold:\n    total -= bulk_discount * (quantity // threshold)",
                      "x", repo)
    assert "does not exist anywhere" in out
    assert "bulk_discount: shop/discounts.py line" in out


def test_undo_reverts_last_batch(tmp_path):
    from agent.tools import write as w
    w.JOURNAL.clear()
    f = tmp_path / "calc.py"
    f.write_text(CALC)
    apply_patch(str(f), "return a + b", "return b + a", tmp_path)
    w.write_file(str(tmp_path / "new.py"), "x = 1\n", tmp_path)
    assert w.undo_last() == "reverted new.py" and not (tmp_path / "new.py").exists()
    assert w.undo_last() == "reverted calc.py" and f.read_text() == CALC
    assert w.undo_last() == "nothing to undo"


def test_undo_reverts_a_whole_rename(tmp_path):
    from agent.tools import write as w
    w.JOURNAL.clear()
    repo = materialise(TASKS_BY_ID["rename_symbol"], tmp_path / "r")
    before = {p: p.read_text() for p in repo.glob("*.py")}
    rename_symbol("multiply", "times", repo)
    w.undo_last()
    assert {p: p.read_text() for p in repo.glob("*.py")} == before


def test_range_read_expands_to_the_enclosing_function_only():
    from agent.tools.read import read_file
    root = Path(__file__).resolve().parent / "fixtures" / "shop"
    src = (root / "shop/orders.py").read_text().splitlines()
    line = next(i for i, l in enumerate(src, 1) if "def subtotal(self)" in l)
    out = read_file(str(root / "shop/orders.py"), root, start=line + 1, end=line + 1)
    assert "def subtotal(self)" in out and "def discounts(self)" not in out
    assert "class Order" not in out


def test_double_escaped_quotes_are_decoded(tmp_path):
    # Arguments exactly as traced on shop_freeship, 2026-11-15.
    repo = materialise(TASKS_BY_ID["shop_freeship"], tmp_path / "r")
    find = 'COUPONS = {\\n    \\"WELCOME5\\": (\\"fixed\\", 500),\\n    \\"SPRING10\\": (\\"rate\\", 0.10),\\n}'
    replace = ('COUPONS = {\\n    \\"WELCOME5\\": (\\"fixed\\", 500),\\n    \\"SPRING10\\": (\\"rate\\", 0.10),'
               '\\n    \\"FREESHIP\\": (\\"shipping\\", 0),\\n}')
    out = apply_patch(str(repo / "shop/discounts.py"), find, replace, repo)
    assert out.startswith("patched"), out
    assert '"FREESHIP": ("shipping", 0),' in (repo / "shop/discounts.py").read_text()


def test_double_escaped_docstring_parses(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")
    out = apply_patch(str(f), "def f():\\n    return 1",
                      'def f():\\n    \\"\\"\\"Return one.\\"\\"\\"\\n    return 1', tmp_path)
    assert out.startswith("patched"), out
    assert '"""Return one."""' in f.read_text()


def test_real_escaped_quote_in_code_still_matches(tmp_path):
    f = tmp_path / "a.py"
    f.write_text('print("say \\"hi\\"")\n')
    out = apply_patch(str(f), 'print("say \\"hi\\"")', 'print("say \\"bye\\"")', tmp_path)
    assert out.startswith("patched"), out
    assert f.read_text() == 'print("say \\"bye\\"")\n'
