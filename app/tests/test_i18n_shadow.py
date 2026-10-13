"""No function may bind a local `_` and also call the translation function `_()` (UnboundLocalError/TypeError at
run time, only on the code path that shows the message)."""

import ast
import unittest
from pathlib import Path

GTK = Path(__file__).resolve().parents[1] / "alphacam_gtk"


class NoShadowedGettext(unittest.TestCase):
    def test_no_local_underscore_with_gettext_calls(self):
        bad = []
        for f in GTK.rglob("*.py"):
            for fn in ast.walk(ast.parse(f.read_text())):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                own = [n for n in ast.walk(fn) if n is not fn]
                stores = {n.id for n in own if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                args = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
                calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_"
                            for n in own)
                if calls and "_" in stores | args:
                    bad.append(f"{f.name}:{fn.lineno}")
        self.assertEqual(bad, [])
