"""Match narrow arithmetic operations across Python and JavaScript sources."""

from __future__ import annotations

import ast
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath

from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.references import JS_SUFFIXES
from pyhyi.rules import Rule
from pyhyi.rules.contract_source import masked_js, sources


def _name(value: str) -> str:
    return value.replace("_", "").casefold()


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    name: str

    @property
    def module(self) -> str:
        return _name(PurePosixPath(self.path).stem)


@dataclass(frozen=True)
class Boundary:
    site: Site
    contract: str
    operands: tuple[str, str]
    op: str


@dataclass(frozen=True)
class Scale:
    site: Site
    operands: tuple[str, str, str]
    digits: int


_ID = r"[A-Za-z_]\w*"
_LIMIT = rf"(?:{_ID}|(?:0|[1-9][0-9]{{0,8}}))"
_JS_FUNCTION = re.compile(rf"\bfunction\s+({_ID})\s*\(([^(){{}}]*)\)\s*(?::\s*{_ID}\s*)?\{{")
_JS_TEST = re.compile(rf"\s*return[ \t]+({_ID})\s*(<=?|>=?)\s*({_LIMIT})\s*;?\s*\}}")
_JS_FIELD_TEST = re.compile(
    rf"\s*return[ \t]*\{{\s*({_ID})\s*:\s*({_ID})\s*(<=?|>=?)\s*({_LIMIT})\s*,?\s*\}}\s*;?\s*\}}"
)
_JS_STEP = re.compile(
    rf"\s*(?:(?:const|let)\s+({_ID})\s*=[^\n;{{}}]+(?:;|\n)\s*)?"
    rf"return[ \t]+Math\.round\(\s*({_ID})\s*/\s*({_ID})\s*\)\s*\*\s*\3\b\s*;?\s*\}}"
)
_JS_SCALE = re.compile(
    rf"(?<![\w.])Math\.round\(\s*\(\s*({_ID})\s*\+\s*({_ID})\s*\*\s*({_ID})\s*\)"
    r"\s*\*\s*(10{0,5})\s*\)\s*/\s*\4\b(?![\d.])"
)
_OPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def _identifier(node: ast.AST | None) -> str:
    return node.id if isinstance(node, ast.Name) else ""


def _numeric_builtins(tree: ast.Module) -> set[str]:
    """Skip a module's arithmetic models when a built-in has an ambiguous binding."""
    available = {"round", "sum"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            available.discard(node.name)
        elif isinstance(node, ast.arg):
            available.discard(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            available.discard(node.id)
        elif isinstance(node, ast.alias):
            if node.name == "*":
                return set()
            available.discard(node.asname or node.name)
    return available


def _function_body(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return body[1:]
    return body


def _sum_order(fn: ast.FunctionDef) -> tuple[str, int, str] | None:
    """Recognize moving built-in rounding across a sum of one sequence."""
    body = _function_body(fn)
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    outer = body[0].value
    if not isinstance(outer, ast.Call) or outer.keywords or not outer.args:
        return None
    inner = outer.args[0]
    if _identifier(outer.func) == "round" and isinstance(inner, ast.Call):
        if _identifier(inner.func) != "sum" or inner.keywords or len(inner.args) != 1:
            return None
        sequence, rounding, order = inner.args[0], outer, "after"
    elif (
        _identifier(outer.func) == "sum"
        and len(outer.args) == 1
        and isinstance(inner, (ast.GeneratorExp, ast.ListComp))
    ):
        if len(inner.generators) != 1 or not isinstance(inner.elt, ast.Call):
            return None
        loop = inner.generators[0]
        rounding = inner.elt
        if (
            loop.ifs
            or loop.is_async
            or _identifier(rounding.func) != "round"
            or not isinstance(loop.target, ast.Name)
            or not rounding.args
            or _identifier(rounding.args[0]) != loop.target.id
        ):
            return None
        sequence, order = loop.iter, "before"
    else:
        return None
    if len(rounding.args) != 2 or rounding.keywords:
        return None
    digits = rounding.args[1]
    if (
        not isinstance(digits, ast.Constant)
        or type(digits.value) is not int
        or not 0 <= digits.value <= 5
    ):
        return None
    seq = _identifier(sequence)
    if seq and any(
        arg.arg == seq for arg in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs
    ):
        return seq, digits.value, order
    return None


def _py_boundary(fn: ast.FunctionDef, site: Site) -> Boundary | None:
    body = _function_body(fn)
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    expr = body[0].value
    contract = fn.name
    if (
        isinstance(expr, ast.Dict)
        and len(expr.keys) == 1
        and isinstance(expr.keys[0], ast.Constant)
        and isinstance(expr.keys[0].value, str)
    ):
        contract = expr.keys[0].value
        expr = expr.values[0]
    if not isinstance(expr, ast.Compare) or len(expr.ops) != 1:
        return None
    op = _OPS.get(type(expr.ops[0]))
    right = expr.comparators[0]
    limit = _identifier(right)
    if isinstance(right, ast.Constant) and type(right.value) is int and 0 <= right.value < 10**9:
        limit = str(right.value)
    pair = (_identifier(expr.left), limit)
    params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
    if op and pair[0] != limit and pair[0] in params and (limit in params or limit.isdigit()):
        return Boundary(Site(site.path, expr.lineno, site.name), contract, pair, op)
    return None


def _py_scales(fn: ast.FunctionDef, path: str) -> Iterator[Scale]:
    # Resolve straight-line arithmetic aliases. Conditional writes and repeated
    # assignments invalidate a binding. Only model operations on annotated numbers.
    params = {a.arg for a in fn.args.args if _identifier(a.annotation) in {"int", "float"}}
    floating = {a.arg for a in fn.args.args if _identifier(a.annotation) == "float"}
    writes = Counter(
        n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    )
    aliases: dict[str, ast.expr] = {}

    def expand(node: ast.expr) -> ast.expr:
        return aliases.get(_identifier(node), node)

    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            key = _identifier(stmt.targets[0])
            if key and writes[key] == 1:
                aliases[key] = stmt.value
        if not isinstance(stmt, ast.Return) or stmt.value is None:
            continue
        for call in ast.walk(stmt.value):
            if (
                not isinstance(call, ast.Call)
                or _identifier(call.func) != "round"
                or len(call.args) != 2
            ):
                continue
            digits = call.args[1]
            if (
                not isinstance(digits, ast.Constant)
                or type(digits.value) is not int
                or not 0 <= digits.value <= 5
            ):
                continue
            expr = expand(call.args[0])
            if not isinstance(expr, ast.BinOp) or not isinstance(expr.op, ast.Add):
                continue
            product = expand(expr.right)
            if not isinstance(product, ast.BinOp) or not isinstance(product.op, ast.Mult):
                continue
            operands = (
                _identifier(expr.left),
                _identifier(product.left),
                _identifier(product.right),
            )
            if (
                len(set(operands)) == 3
                and set(operands) <= params
                and floating.intersection(operands)
                and params.isdisjoint(writes)
            ):
                yield Scale(Site(path, call.lineno, fn.name), operands, digits.value)


def _decimal_step(fn: ast.FunctionDef, path: str, imports: set[str]) -> Site | None:
    if not {"Decimal", "ROUND_HALF_UP"} <= imports:
        return None
    decimals = {a.arg for a in fn.args.args if _identifier(a.annotation) == "Decimal"}
    default_modes = {
        a.arg
        for a, val in zip(reversed(fn.args.args), reversed(fn.args.defaults), strict=False)
        if _identifier(val) == "ROUND_HALF_UP"
    }
    assignments: dict[str, ast.expr] = {}
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            assignments[_identifier(stmt.targets[0])] = stmt.value
        if not isinstance(stmt, ast.Return) or stmt.value is None:
            continue
        expr = stmt.value
        # An optional one-argument wrapper can format/quantize the product.
        # The finding models the rounding operation inside that wrapper.
        if isinstance(expr, ast.Call) and len(expr.args) == 1:
            expr = expr.args[0]
        if not isinstance(expr, ast.BinOp) or not isinstance(expr.op, ast.Mult):
            continue
        call = assignments.get(_identifier(expr.left), expr.left)
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        divisor = call.func.value
        if (
            call.func.attr == "to_integral_value"
            and isinstance(divisor, ast.BinOp)
            and isinstance(divisor.op, ast.Div)
            and _identifier(expr.right) == _identifier(divisor.right)
            and {_identifier(divisor.left), _identifier(divisor.right)} <= decimals
            and any(
                kw.arg == "rounding" and _identifier(kw.value) in default_modes | {"ROUND_HALF_UP"}
                for kw in call.keywords
            )
        ):
            return Site(path, call.lineno, fn.name)
    return None


def _js_round(value: float) -> int:
    lower = math.floor(value)
    return lower + (value - lower >= 0.5)


def _step_example() -> str:
    # A product at an exact positive half-step need not be representable in binary.
    for units in range(1, 101):
        for hundredths in range(101, 200):
            factor = Decimal(hundredths).scaleb(-2)
            exact = units * factor
            if exact % 1 != Decimal("0.5"):
                continue
            binary = _js_round(units * float(factor))
            decimal = exact.to_integral_value(rounding=ROUND_HALF_UP)
            if binary != decimal:
                return (
                    f"Modeled product {units} * {factor} at step 1 gives {binary} with "
                    f"binary64/Math.round and {decimal} with decimal half-up."
                )
    raise AssertionError("expected a differing finite half-step")


def _boundaries(rule: Rule, items: list[Boundary]) -> Iterator[Finding]:
    groups: dict[tuple[str, tuple[str, ...], str], list[Boundary]] = defaultdict(list)
    for b in items:
        if _name(b.contract) in {
            "run",
            "main",
            "check",
            "validate",
            "test",
            "ok",
            "valid",
            "result",
        }:
            continue
        groups[_name(b.contract), tuple(_name(x) for x in b.operands), b.op[0]].append(b)
    for group in groups.values():
        if len(group) != 2:
            continue
        a, b = sorted(group, key=lambda x: x.site.path)
        if (a.site.path, a.site.name) == (b.site.path, b.site.name) or a.op == b.op:
            continue
        left, right = a.operands
        equality = f"{left}={right}" if right.isdigit() else f"{left}={right}=1"
        yield rule.finding(
            f"Predicates in {a.site.name} and {b.site.name} match on '{a.contract}'. "
            f"For {equality}, '{a.op}' returns {a.op.endswith('=')} "
            f"and '{b.op}' returns {b.op.endswith('=')}.",
            path=a.site.path,
            line=a.site.line,
            key=f"boundary:{a.site.name}:{b.site.path}:{b.site.name}:{left}:{right}:{a.op}:{b.op}",
            hint=f"Compare the intended boundaries with {b.site.path}:{b.site.line}.",
        )


def observations(ctx: Context, rule: Rule) -> Iterator[Finding]:
    bounds: list[Boundary] = []
    js_steps: dict[str, list[Site]] = defaultdict(list)
    js_scales: dict[tuple[str, int], list[Scale]] = defaultdict(list)
    for path, text in sources(ctx, JS_SUFFIXES):
        code = masked_js(text)
        for m in _JS_FUNCTION.finditer(code):
            site = Site(path, code.count("\n", 0, m.start()) + 1, m[1])
            params = {p.partition(":")[0].strip() for p in m[2].split(",")}
            field_boundary = _JS_FIELD_TEST.match(code, m.end())
            boundary = field_boundary or _JS_TEST.match(code, m.end())
            if boundary:
                left, op, right = boundary.groups()[-3:]
                contract = field_boundary[1] if field_boundary else site.name
                if left in params and (right in params or right.isdigit()) and left != right:
                    bounds.append(Boundary(site, contract, (left, right), op))
            step = _JS_STEP.match(code, m.end())
            if step and step[2] in params and (step[3] in params or step[1] == step[3]):
                js_steps[_name(site.name)].append(site)
        for m in _JS_SCALE.finditer(code):
            line = code.count("\n", 0, m.start()) + 1
            site = Site(path, line, "Math.round")
            digits = len(m[4]) - 1
            js_scales[site.module, digits].append(Scale(site, (m[1], m[2], m[3]), digits))

    py_steps: dict[str, list[Site]] = defaultdict(list)
    py_scales: dict[tuple[str, int], list[Scale]] = defaultdict(list)
    sums: dict[tuple[str, str, int], list[tuple[Site, str]]] = defaultdict(list)
    scale_modules = {module for module, _ in js_scales}
    for path, text in sources(ctx, (".py",)):
        if "def " not in text or not any(
            op in text for op in ("<", ">", "round", "to_integral_value")
        ):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            continue
        imports = {
            a.name
            for stmt in tree.body
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "decimal" and not stmt.level
            for a in stmt.names
            if not a.asname
        }
        builtin_calls = _numeric_builtins(tree) if "round(" in text else set()
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            site = Site(path, fn.lineno, fn.name)
            bound = _py_boundary(fn, site)
            if bound:
                bounds.append(bound)
            if {"sum", "round"} <= builtin_calls and (shape := _sum_order(fn)):
                sequence, digits, order = shape
                sums[_name(fn.name), sequence, digits].append((site, order))
            if site.module in scale_modules and "round" in builtin_calls:
                for scale in _py_scales(fn, path):
                    py_scales[site.module, scale.digits].append(scale)
            if _name(fn.name) in js_steps:
                peer = _decimal_step(fn, path, imports)
                if peer:
                    py_steps[_name(fn.name)].append(peer)

    yield from _boundaries(rule, bounds)
    for (_, sequence, digits), sites in sums.items():
        if len(sites) != 2 or sites[0][1] == sites[1][1] or sites[0][0].path == sites[1][0].path:
            continue
        (after, _), (before, _) = sorted(sites, key=lambda pair: pair[1])
        value = 1 / 2 ** (digits + 1)
        values = [value, value]
        rounded_sum = round(sum(values), digits)
        rounded_items = sum(round(v, digits) for v in values)
        yield rule.finding(
            f"For {sequence}={values}, {after.name} rounds the sum to {rounded_sum}, "
            f"while {before.name} sums rounded values to {rounded_items}.",
            path=after.path,
            line=after.line,
            key=f"sum-order:{after.name}:{before.path}:{digits}",
            hint=f"Compare the intended rounding order with {before.path}:{before.line}.",
        )
    for key, python in py_steps.items():
        if len(python) == len(js_steps[key]) == 1:
            client, server = js_steps[key][0], python[0]
            yield rule.finding(
                f"Review matching step-rounding helpers {client.name} and {server.name}. "
                + _step_example(),
                path=client.path,
                line=client.line,
                key=f"decimal-step:{client.name}:{server.path}:{server.name}",
                hint=f"Compare inputs and permitted steps with {server.path}:{server.line}.",
            )
    for key, servers in py_scales.items():
        clients = js_scales[key]
        if len(servers) != 1 or len(clients) != 1:
            continue
        server, client = servers[0], clients[0]
        if (
            sum(_name(a) == _name(b) for a, b in zip(client.operands, server.operands, strict=True))
            < 2
        ):
            continue
        digits = client.digits
        # This binary-exact tie stays exact after multiplication by 10**digits.
        value = 1 / 2 ** (digits + 1)
        multiplier = 10**digits
        js = _js_round(value * multiplier) / multiplier
        py = round(value, digits)
        if js == py:
            continue
        yield rule.finding(
            f"Matching modules round similar sums of products to precision {digits}. "
            f"For the intermediate value {value:g}, Math.round scaling gives {js:g} "
            f"and Python round gives {py:g}.",
            path=client.site.path,
            line=client.site.line,
            key=f"scaled-sum:{server.site.path}:{server.site.name}:{digits}",
            hint=f"Check whether this input is allowed at {server.site.path}:{server.site.line}.",
        )
