"""Find paired calculations that handle a two-dimensional limit differently."""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.rules import Rule
from pyhyi.rules.contract_source import masked_js, sources

_FUNCTION = re.compile(r"\bfunction\s+(\w+)\s*\([^(){}]*\)\s*\{")
_PROP = re.compile(r"\b(\w+)\s*\??\.\s*(\w+)\b")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_BRACE = re.compile(r"[{}]")
_MAP = re.compile(r"\bconst\s+(\w+)\s*=\s*[\w.]+\.map\s*\(\s*\(?\s*(\w+)\s*\)?\s*=>\s*\{")
_NESTED = re.compile(r"(?:=>|\bfunction\s*\w*\s*\([^(){}]*\))\s*\{")
_RETURN = re.compile(r"\breturn\s*\{")
Fields = frozenset[str]
Aliases = dict[str, tuple[str, str] | None]


def _axis(key: str) -> tuple[str, str] | None:
    """Split an axis from its role and units, preserving the field spelling for matching."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().split("_")
    for axis in ("width", "height"):
        if axis in words:
            return axis, "_".join("*" if word == axis else word for word in words)
    return None


def _read(node: ast.expr) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    if isinstance(node, ast.Subscript):
        base, key = node.value, node.slice
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        base, key = node.func.value, node.args[0]
    else:
        return None
    if isinstance(base, ast.Name) and isinstance(key, ast.Constant) and isinstance(key.value, str):
        return base.id, key.value
    return None


def _field(node: ast.expr, aliases: Aliases) -> tuple[str, str] | None:
    return aliases.get(node.id) if isinstance(node, ast.Name) else _read(node)


def _edges(expr: ast.expr, aliases: Aliases) -> Iterator[tuple[str, str, str]]:
    for compare in ast.walk(expr):
        if not isinstance(compare, ast.Compare):
            continue
        left = compare.left
        for operator, right in zip(compare.ops, compare.comparators, strict=True):
            if isinstance(operator, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                # An offset on one side of a comparison may represent a tolerance.
                sides = [
                    {
                        f
                        for n in ast.walk(side)
                        if isinstance(n, ast.expr) and (f := _field(n, aliases)) and _axis(f[1])
                    }
                    for side in (left, right)
                ]
                if all(len(fields) == 1 for fields in sides):
                    (row1, key1), (row2, key2) = (next(iter(fields)) for fields in sides)
                    if row1 == row2 and key1 != key2:
                        yield row1, key1, key2
            left = right


@dataclass(frozen=True)
class Guard:
    path: str
    name: str
    owner: str
    line: int
    role1: Fields
    role2: Fields
    outputs: Fields


def _bindings(scope: ast.AST) -> Counter[str]:
    """Count bindings without descending into unrelated Python scopes."""
    names: Counter[str] = Counter()
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[n.name] += 1
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names[n.id] += 1
        elif isinstance(n, ast.arg):
            names[n.arg] += 1
        elif isinstance(n, ast.alias):
            names[n.asname or n.name.split(".")[0]] += 1
        elif isinstance(n, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)) and n.name:
            names[n.name] += 1
        elif isinstance(n, ast.MatchMapping) and n.rest:
            names[n.rest] += 1
        todo.extend(ast.iter_child_nodes(n))
    return names


def _guard_pairs(fn: ast.FunctionDef) -> Iterator[tuple[int, str, Fields, Fields]]:
    aliases: Aliases = {}
    helpers = {n.name: n for n in fn.body if isinstance(n, ast.FunctionDef)}
    if helpers:
        bindings = _bindings(fn)
        helpers = {k: v for k, v in helpers.items() if bindings[k] == 1}
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            dest, src = stmt.targets[0], stmt.value
            bindings = (
                zip(dest.elts, src.elts, strict=True)
                if isinstance(dest, ast.Tuple)
                and isinstance(src, ast.Tuple)
                and len(dest.elts) == len(src.elts)
                else [(dest, src)]
            )
            for var, value in bindings:
                if isinstance(var, ast.Name):
                    aliases[var.id] = _field(value, aliases)
        if not isinstance(stmt, ast.If) or not any(isinstance(n, ast.Raise) for n in stmt.body):
            continue
        edges = list(_edges(stmt.test, aliases))
        for call in ast.walk(stmt.test):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            helper = helpers.get(call.func.id)
            if (
                helper is None
                or helper.lineno >= stmt.lineno
                or len(helper.body) != 1
                or len(call.args) != len(helper.args.args)
                or call.keywords
            ):
                continue
            ret = helper.body[0]
            if isinstance(ret, ast.Return) and ret.value is not None:
                params = {
                    arg.arg: _field(value, aliases)
                    for arg, value in zip(helper.args.args, call.args, strict=True)
                }
                edges.extend(_edges(ret.value, params))
        pairs: dict = defaultdict(lambda: defaultdict(dict))
        for root, left, right in edges:
            a, b = _axis(left), _axis(right)
            if a and b and a[0] == b[0] and a[1] != b[1]:
                pair = tuple(sorted((a[1], b[1])))
                pairs[(root, pair)][a[1]][a[0]] = left
                pairs[(root, pair)][b[1]][b[0]] = right
        for (root, (a, b)), roles in pairs.items():
            if len(roles[a]) == len(roles[b]) == 2:
                yield stmt.lineno, root, frozenset(roles[a].values()), frozenset(roles[b].values())


def _output_fields(fn: ast.FunctionDef, row: str, guard_line: int) -> Fields:
    """Follow one record from the guard to its return, without joining alternatives."""
    last = fn.body[-1]
    if not isinstance(last, ast.Return) or last.lineno <= guard_line:
        return frozenset()
    fields = set()
    for stmt in fn.body[:-1]:
        # A later rebind makes uses of the same variable different records.
        if any(
            isinstance(n, ast.Name) and n.id == row and isinstance(n.ctx, (ast.Store, ast.Del))
            for n in ast.walk(stmt)
        ):
            if stmt.lineno >= guard_line:
                return frozenset()
            fields.clear()
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            method = stmt.value.func
            if (
                isinstance(method, ast.Attribute)
                and method.attr == "update"
                and isinstance(method.value, ast.Name)
                and method.value.id == row
            ):
                fields.update(k.arg for k in stmt.value.keywords if k.arg)
    if isinstance(last.value, ast.Name) and last.value.id == row:
        return frozenset(fields)
    if isinstance(last.value, ast.Dict) and any(
        k is None and isinstance(v, ast.Name) and v.id == row
        for k, v in zip(last.value.keys, last.value.values, strict=True)
    ):
        fields.update(
            k.value
            for k in last.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
        return frozenset(fields)
    return frozenset()


def _guards(path: str, text: str) -> Iterator[Guard]:
    if "width" not in text.lower() or "height" not in text.lower():
        return
    try:
        module = ast.parse(text)
    except (SyntaxError, RecursionError, ValueError):
        return
    functions = [n for n in module.body if isinstance(n, ast.FunctionDef)]
    guards = {fn: pairs for fn in functions if (pairs := list(_guard_pairs(fn)))}
    if not guards:
        return
    global_names = _bindings(module)
    callers = defaultdict(list)
    guard_names = {fn.name for fn in guards}
    for caller in functions:
        for stmt in caller.body:
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            if (
                isinstance(call.func, ast.Name)
                and call.func.id in guard_names
                and call.args
                and isinstance(call.args[0], ast.Name)
                and not _bindings(caller)[call.func.id]
            ):
                callers[call.func.id].append((caller, call.args[0].id, stmt.lineno))
    for fn, pairs in guards.items():
        if global_names[fn.name] != 1:
            continue
        for line, root, a, b in pairs:
            owners = [(fn, root, line)]
            if fn.args.args and fn.args.args[0].arg == root:
                owners.extend(callers[fn.name])
            for owner, var, call_line in owners:
                outputs = _output_fields(owner, var, call_line)
                if len(outputs) >= 2:
                    yield Guard(path, fn.name, owner.name, line, a, b, outputs)


def _close(text: str, start: int) -> int:
    depth = 1
    for brace in _BRACE.finditer(text, start):
        depth += 1 if brace[0] == "{" else -1
        if depth == 0:
            return brace.start()
    return len(text)


def _return_fields(body: str) -> dict[str, str]:
    """Keep direct keys of one returned literal. Alternative returns are ambiguous."""
    returns = list(_RETURN.finditer(body))
    if len(returns) != 1:
        return {}
    start = returns[0].end()
    fields = {}
    level = 0
    for idx in range(start, len(body)):
        char = body[idx]
        if level == 0 and char in ",}":
            field = body[start:idx].strip()
            name = _IDENT.match(field)
            if name:
                rest = field[name.end() :].lstrip()
                if not rest or rest.startswith(":"):
                    fields[name[0]] = rest[1:].strip() if rest else name[0]
            if char == "}":
                return fields
            start = idx + 1
        elif char in "([{":
            level += 1
        elif char in ")]}":
            level -= 1
    return {}


def _direct_scope(body: str) -> str:
    """Mask function bodies that compute a separate result on the client."""
    parts = []
    pos = 0
    for func in _NESTED.finditer(body):
        if func.start() >= pos:
            end = min(_close(body, func.end()) + 1, len(body))
            parts.extend((body[pos : func.end()], " " * (end - func.end() - 1), "}"))
            pos = end
    parts.append(body[pos:])
    return "".join(parts)


def _client_records(body: str) -> Iterator[tuple[dict[str, str], str, str | None]]:
    outer = _direct_scope(body)
    result = _return_fields(outer)
    if not re.fullmatch(r"\w+\.length\s*===\s*0", result.get("valid", "")):
        return
    yield result, outer, None
    for mapped in _MAP.finditer(outer):
        # Only a map whose result is part of the validated object's return value.
        if mapped[1] in result.values():
            inside = _direct_scope(body[mapped.end() : _close(body, mapped.end())])
            yield _return_fields(inside), inside, mapped[2]


def _bodies(text: str) -> Iterator[tuple[str, int, str]]:
    for match in _FUNCTION.finditer(text):
        end = _close(text, match.end())
        if end < len(text):
            yield match[1], text.count("\n", 0, match.start()) + 1, text[match.end() : end]


def _matches(body: str, index: dict[str, list[Guard]]):
    for output, scope, item in _client_records(body):
        props: dict[str, set[str]] = defaultdict(set)
        for row, key in _PROP.findall(scope):
            if item is None or row == item:
                props[row].add(key)
        candidates = {guard for key in output for guard in index.get(key, ())}
        for guard in candidates:
            shared = frozenset(output) & guard.outputs - guard.role1 - guard.role2
            if len(shared) < 2:
                continue
            for seen, other in ((guard.role1, guard.role2), (guard.role2, guard.role1)):
                if any(re.search(rf"\b{re.escape(key)}\b", body) for key in other):
                    continue
                rows = [r for r, fields in props.items() if seen <= fields]
                if len(rows) == 1 and not re.search(
                    rf"\b{re.escape(rows[0])}\s*\[|[(,]\s*{re.escape(rows[0])}\s*[,)]", scope
                ):
                    yield guard, seen, other, shared


def observations(ctx: Context, rule: Rule) -> Iterator[Finding]:
    """Suggest comparing validation paths without assuming an equivalent UI contract."""
    guards = [g for path, text in sources(ctx, (".py",)) for g in _guards(path, text)]
    if not guards:
        return
    index = defaultdict(list)
    for guard in guards:
        for field in guard.outputs:
            index[field].append(guard)
    for path, text in sources(ctx, (".js", ".mjs", ".ts")):
        if "width" not in text.lower() or "height" not in text.lower():
            continue
        for name, line, body in _bodies(masked_js(text)):
            matches = defaultdict(set)
            for guard, seen, other, shared in _matches(body, index):
                matches[(seen, other)].add((guard, shared))
            for (seen, other), owners in sorted(
                matches.items(), key=lambda item: (sorted(item[0][0]), sorted(item[0][1]))
            ):
                if len(owners) != 1:
                    continue
                guard, shared = owners.pop()
                first = ", ".join(sorted(seen))
                second = ", ".join(sorted(other))
                yield rule.finding(
                    f"Review candidate: {name} and {guard.owner} return fields "
                    f"{', '.join(sorted(shared))}. {guard.name} compares {first} with {second}. "
                    f"Only {first} appeared in direct property reads in {name}.",
                    path=path,
                    line=line,
                    key=f"dimension:{name}:{guard.path}:{guard.owner}:{guard.name}:{second}:{first}",
                    hint=f"Compare boundary behavior with {guard.path}:{guard.line}. "
                    "Verify field availability and whether both layers promise equal validation.",
                )
