"""Compare small assertion predicates over ordinary built-in values."""

from __future__ import annotations

import ast
import math
import operator
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import product

Value = int | float | str | bool | None
Assignment = dict[str, Value]
Predicate = Callable[[Assignment], bool]

_COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_ORDERED = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


class _Unsupported(Exception):
    """The expression falls outside the comparison model."""


def _literal(node: ast.expr) -> Value:
    if isinstance(node, ast.Constant) and (
        node.value is None or type(node.value) in (bool, int, str)
    ):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) is int
    ):
        return -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
    raise _Unsupported


@dataclass
class _Domain:
    literals: list[Value]
    ordered: bool = False
    opaque: bool = False
    constructor: str = ""

    def samples(self) -> list[Value]:
        # Every integer comparison is constant between consecutive cut points.
        # For equality/membership, one unseen string represents the rest.
        integers = {0, 1, -1}
        for value in self.literals:
            if type(value) is int:
                integers.update((value - 1, value, value + 1))
        if self.constructor == "len":
            return sorted(v for v in integers if 0 <= v <= sys.maxsize)
        if self.constructor in {"bool", "isinstance", "issubclass", "all", "any", "hasattr"}:
            return [False, True]
        if self.constructor == "int":
            return sorted(integers)
        fractional = []
        if self.ordered:
            # An edit can widen the integer region while rejecting e.g. 0.5.
            # At these cut points adjacent half-integers are exactly representable.
            if any(abs(value) >= 2**52 for value in integers):
                raise _Unsupported
            fractional = [value + 0.5 for value in sorted(integers)]
            fractional.append(math.nan)
        strings = {s for s in self.literals if isinstance(s, str)}
        other = "other"
        while other in strings:
            other += "_"
        if self.constructor == "str":
            return sorted(strings | {"", other})
        return [
            0,
            1,
            *sorted(integers - {0, 1}),
            *fractional,
            *sorted(strings | {"", other}),
            None,
            False,
            True,
        ]


class _Model:
    def __init__(self) -> None:
        self.domains: dict[str, _Domain] = defaultdict(lambda: _Domain([]))
        self.uses: Counter[str] = Counter()

    def subject(self, node: ast.expr) -> str:
        if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
            raise _Unsupported
        key = ast.unparse(node)
        self.domains[key].opaque = not isinstance(node, ast.Name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            self.domains[key].constructor = node.func.id
        self.uses[key] += 1
        return key

    def comparison(self, expr: ast.Compare) -> Predicate:
        if len(expr.ops) != 1:
            operands = [expr.left, *expr.comparators]
            parts = [
                self.comparison(ast.Compare(operands[i], [op], [operands[i + 1]]))
                for i, op in enumerate(expr.ops)
            ]
            return lambda env: all(part(env) for part in parts)
        left, op, right = expr.left, expr.ops[0], expr.comparators[0]
        if isinstance(op, (ast.In, ast.NotIn)):
            if not isinstance(right, (ast.Set, ast.List, ast.Tuple)):
                raise _Unsupported
            values = tuple(_literal(n) for n in right.elts)
            key = self.subject(left)
            self.domains[key].literals.extend(values)
            include = isinstance(op, ast.In)
            return lambda env: (env[key] in values) == include
        operation = _COMPARISONS.get(type(op))
        if operation is None:
            raise _Unsupported
        try:
            value = _literal(right)
        except _Unsupported:
            value = _literal(left)
            key = self.subject(right)
            reversed_args = True
        else:
            key = self.subject(left)
            reversed_args = False
        if isinstance(op, (ast.Is, ast.IsNot)) and value is not None:
            raise _Unsupported
        if isinstance(op, _ORDERED):
            if type(value) is not int:
                raise _Unsupported
            self.domains[key].ordered = True
        self.domains[key].literals.append(value)
        if reversed_args:
            return lambda env: operation(value, env[key])
        return lambda env: operation(env[key], value)

    def parse(self, expr: ast.expr) -> Predicate:
        if isinstance(expr, ast.Compare):
            return self.comparison(expr)
        if isinstance(expr, ast.BoolOp):
            parts = [self.parse(n) for n in expr.values]
            combine = all if isinstance(expr.op, ast.And) else any
            return lambda env: combine(p(env) for p in parts)
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
            child = self.parse(expr.operand)
            return lambda env: not child(env)
        if isinstance(expr, ast.Constant) and type(expr.value) is bool:
            return lambda env: expr.value
        key = self.subject(expr)
        return lambda env: bool(env[key])


def widening(before: ast.expr, after: ast.expr) -> str | None:
    """Return a concrete newly accepted assignment for a strict modeled widening."""
    model = _Model()
    try:
        old = model.parse(before)
        previous_uses = model.uses.copy()
        model.uses.clear()
        new = model.parse(after)
        domains = model.domains
        if not previous_uses.keys() & model.uses.keys() or len(domains) > 3:
            return None
        # Multiple reads of a property or calls can return different values.
        if any(
            d.opaque and (len(domains) > 1 or max(previous_uses[k], model.uses[k]) > 1)
            for k, d in domains.items()
        ):
            return None
        if any(
            len(d.literals) > 32
            or (
                d.ordered
                and any(type(value) is not int and value is not None for value in d.literals)
            )
            for d in domains.values()
        ):
            return None
        samples = [d.samples() for d in domains.values()]
        combinations = 1
        for cells in samples:
            combinations *= len(cells)
        if combinations > 4096:
            return None
        witness = None
        old_can_pass = False
        for values in product(*samples):
            env = dict(zip(domains, values, strict=True))
            was, now = _passes(old, env), _passes(new, env)
            if was and not now:
                return None
            old_can_pass |= was
            if now and not was and witness is None:
                witness = ", ".join(f"{k}={v!r}" for k, v in env.items())
        return witness if old_can_pass else None
    except (_Unsupported, RecursionError):
        return None


def _passes(predicate: Predicate, env: Assignment) -> bool:
    try:
        return predicate(env)
    except TypeError:
        # An ordering comparison against a nonnumeric built-in cannot pass.
        return False
