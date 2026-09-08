"""Deterministic tracer for small, self-contained Python code snippets.

Category S of the 2026-09-05 expanded questionnaire ("Verificação de
código simples"): "what does this code print?" has exactly one correct
answer, computable by actually running the code -- an LLM asked to
mentally trace a for-loop or an if/else is exactly the kind of task that
looks easy and is quietly wrong often enough to matter.

Like deterministic_arithmetic.py, this NEVER uses eval()/exec() --
it walks a restricted AST and executes only a small, explicit subset of
Python: integer/string/list literals, name assignment, +-*/ between
already-known values, len(), print(), if/else with a single comparison,
and for-loops over range(N). Anything outside that subset (imports,
function/class defs, attribute access, comprehensions, while-loops,
arbitrary calls) makes compute() return None -- callers fall through to
the normal LLM path, same discipline as every other deterministic
executor in this package. This is a tracer for the exact shapes the
questionnaire uses, not a Python sandbox.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field

_CODE_BLOCK_RE = re.compile(r"c[óo]digo\s*:\s*\n?(.+)", re.IGNORECASE | re.DOTALL)

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ALLOWED_COMPARES = {
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class CodeTraceError(ValueError):
    """Raised internally for any construct outside the supported subset."""


@dataclass(frozen=True)
class CodeTraceResult:
    printed_lines: list[str] = field(default_factory=list)
    formatted: str = ""


def extract_code_block(text: str) -> str | None:
    match = _CODE_BLOCK_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


class _Tracer:
    def __init__(self) -> None:
        self.env: dict[str, object] = {}
        self.output: list[str] = []

    def run(self, source: str) -> None:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            raise CodeTraceError(f"not valid Python: {exc}") from exc
        self._exec_body(tree.body)

    def _exec_body(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            self._exec_stmt(stmt)

    def _exec_stmt(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise CodeTraceError("only simple single-name assignment is supported")
            self.env[stmt.targets[0].id] = self._eval(stmt.value)
            return

        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                values = [self._eval(arg) for arg in call.args]
                self.output.append(" ".join(str(v) for v in values))
                return
            raise CodeTraceError("only print(...) calls are supported as statements")

        if isinstance(stmt, ast.If):
            if self._eval_bool(stmt.test):
                self._exec_body(stmt.body)
            else:
                self._exec_body(stmt.orelse)
            return

        if isinstance(stmt, ast.For):
            if not isinstance(stmt.target, ast.Name):
                raise CodeTraceError("only a single loop variable is supported")
            iterable = self._eval(stmt.iter)
            if not isinstance(iterable, range):
                raise CodeTraceError("only for-loops over range(...) are supported")
            for value in iterable:
                self.env[stmt.target.id] = value
                self._exec_body(stmt.body)
            return

        raise CodeTraceError(f"unsupported statement: {ast.dump(stmt)}")

    def _eval_bool(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise CodeTraceError("only a single comparison is supported")
            op_type = type(node.ops[0])
            if op_type not in _ALLOWED_COMPARES:
                raise CodeTraceError(f"unsupported comparison: {ast.dump(node)}")
            left = self._eval(node.left)
            right = self._eval(node.comparators[0])
            return _ALLOWED_COMPARES[op_type](left, right)
        result = self._eval(node)
        return bool(result)

    def _eval(self, node: ast.expr):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.env:
                raise CodeTraceError(f"use of undefined name {node.id!r}")
            return self.env[node.id]
        if isinstance(node, ast.List):
            return [self._eval(elt) for elt in node.elts]
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "len" and len(node.args) == 1:
                value = self._eval(node.args[0])
                return len(value)
            if node.func.id == "range":
                args = [self._eval(arg) for arg in node.args]
                return range(*args)
        raise CodeTraceError(f"unsupported expression: {ast.dump(node)}")


def compute(text: str) -> CodeTraceResult | None:
    """Extract a "Código:" block from ``text`` and trace it, returning the
    collected print() output. Returns None (never raises) when there is
    no code block, or the code uses anything outside the small supported
    subset -- callers fall through to the normal LLM path in that case."""
    code = extract_code_block(text)
    if code is None:
        return None

    tracer = _Tracer()
    try:
        tracer.run(code)
    except CodeTraceError:
        return None

    formatted = ", ".join(tracer.output)
    return CodeTraceResult(printed_lines=tracer.output, formatted=formatted)
