"""Static observations about Python test definitions and their Git counterparts."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import ClassVar

from pyhyi import git
from pyhyi.context import MAX_TEXT_BYTES, Context
from pyhyi.findings import Finding, Severity
from pyhyi.rules import Rule

Function = ast.FunctionDef | ast.AsyncFunctionDef
Imports = dict[str, tuple[str, int]]
_CASES = {"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"}


def _test_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _bindings(
    body: list[ast.stmt], *, count_mutations: bool = True, global_writes: set[str] | None = None
) -> Counter[str]:
    """Count potential bindings, including conditional ones, in this namespace."""
    names: Counter[str] = Counter()
    declared_global: set[str] = set()
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] += 1
            if global_writes is not None:
                _bindings(node.body, global_writes=global_writes)
            pending.extend(
                child for child in ast.iter_child_nodes(node) if not isinstance(child, ast.stmt)
            )
            continue
        if isinstance(node, ast.Global):
            declared_global.update(node.names)
        elif isinstance(node, ast.alias):
            names[node.asname or node.name.split(".")[0]] += 1
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names[node.id] += 1
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.ExceptHandler)) and node.name:
            names[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names[node.rest] += 1
        elif (
            count_mutations
            and isinstance(node, ast.Attribute)
            and node.attr != "__test__"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                names[root.id] += 1
        pending.extend(ast.iter_child_nodes(node))
    if global_writes is not None:
        global_writes.update(declared_global.intersection(names))
    return names


def _imports(module: ast.Module, bindings: Counter[str]) -> Imports:
    imports = {}
    for stmt in module.body:
        if isinstance(stmt, ast.Assign):
            expr = stmt.value
            if isinstance(expr, ast.Call) and not expr.args:
                expr = expr.func
            if _resolve(expr, imports) == "pytest.fixture":
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and bindings[target.id] == 1:
                        imports[target.id] = ("pytest.fixture", stmt.lineno)
            continue
        if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(stmt, ast.ImportFrom) and (
            stmt.level or stmt.module not in {"unittest", "pytest"}
        ):
            continue
        for alias in stmt.names:
            name = alias.asname or alias.name
            target = (
                f"{stmt.module}.{alias.name}" if isinstance(stmt, ast.ImportFrom) else alias.name
            )
            if bindings[name] == 1 and target in {
                "unittest",
                "pytest",
                "pytest.mark",
                "pytest.fixture",
                *_CASES,
            }:
                imports[name] = (target, stmt.lineno)
    return imports


def _dotted_name(expr: ast.expr) -> str:
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    return ".".join([expr.id, *reversed(parts)]) if isinstance(expr, ast.Name) else ""


def _resolve(expr: ast.expr, imports: Imports) -> str | None:
    root, dot, rest = _dotted_name(expr).partition(".")
    if root in imports:
        target, line = imports[root]
        if line < expr.lineno:
            return target + dot + rest
    return None


def _disabled_tests(body: list[ast.stmt]) -> set[str]:
    """Static __test__ flags in this namespace. An empty name denotes the namespace itself."""
    disabled = set()
    for stmt in body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            continue
        for target in targets:
            owner, _, attr = _dotted_name(target).rpartition(".")
            if attr == "__test__":
                if isinstance(stmt.value, ast.Constant) and stmt.value.value is True:
                    disabled.discard(owner)
                else:
                    disabled.add(owner)
    return disabled


@dataclass
class _Test:
    body: list[ast.stmt]
    skips: list[int]
    unknown_decorators: list[ast.expr]
    equality_method: bool


def _methods(
    body: list[ast.stmt], scope: Counter[str], imports: Imports, *, equality: bool = False
) -> Iterator[tuple[str, _Test]]:
    if "*" in scope:
        return
    for func in body:
        if (
            not isinstance(func, Function)
            or not func.name.startswith("test")
            or scope[func.name] != 1
        ):
            continue
        skips = []
        unknown = []
        for decorator in func.decorator_list:
            expr = decorator.func if isinstance(decorator, ast.Call) else decorator
            target = _resolve(expr, imports)
            if target == "pytest.fixture":
                break
            if target == "pytest.mark.skip" and (
                not isinstance(decorator, ast.Call) or _skip_arguments(decorator)
            ):
                skips.append(decorator.lineno)
            elif target not in {
                "pytest.mark.skipif",
                "pytest.mark.xfail",
                "pytest.mark.parametrize",
            }:
                unknown.append(decorator)
        else:
            args = func.args.posonlyargs + func.args.args
            ordinary = (
                equality
                and len(skips) == len(func.decorator_list)
                and bool(args)
                and args[0].arg == "self"
                and "self" not in _bindings(func.body, count_mutations=False)
            )
            yield func.name, _Test(func.body, skips, unknown, ordinary)


def _overrides_equality(cls: ast.ClassDef, scope: Counter[str]) -> bool:
    if {"assertEqual", "__getattribute__"}.intersection(scope):
        return True
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "assertEqual"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and (not isinstance(node.args[1], ast.Constant) or node.args[1].value == "assertEqual")
        ):
            return True
    return False


def _tests(source: str | None) -> dict[str, _Test]:
    if source is None:
        return {}
    try:
        module = ast.parse(source.removeprefix("\ufeff"))
    except (SyntaxError, ValueError, RecursionError):
        return {}
    disabled = _disabled_tests(module.body)
    if "" in disabled:
        return {}
    # Only descend into nested namespaces for bindings when global writes are possible.
    global_writes: set[str] = set()
    bindings = _bindings(module.body, global_writes=global_writes if "global" in source else None)
    bindings.update(global_writes)
    if "*" in bindings:
        return {}
    imports = _imports(module, bindings)
    tests = dict(_methods(module.body, bindings, imports))
    for cls in module.body:
        if (
            not isinstance(cls, ast.ClassDef)
            or bindings[cls.name] != 1
            or cls.keywords
            or cls.name in disabled
        ):
            continue
        case = len(cls.bases) == 1 and _resolve(cls.bases[0], imports) in _CASES
        if not case and (cls.bases or not cls.name.startswith("Test")):
            continue
        scope = _bindings(cls.body)
        local_disabled = _disabled_tests(cls.body)
        if "" in local_disabled:
            continue
        if not case and {"__init__", "__new__"}.intersection(scope):
            continue
        visible = {key: value for key, value in imports.items() if key not in scope}
        equality = case and not cls.decorator_list and not _overrides_equality(cls, scope)
        for name, test in _methods(cls.body, scope, visible, equality=equality):
            if name not in local_disabled:
                tests[f"{cls.name}.{name}"] = test
    return {name: test for name, test in tests.items() if name not in disabled}


def _skip_arguments(call: ast.Call) -> bool:
    if call.args:
        return (
            len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        )
    return not call.keywords or (len(call.keywords) == 1 and call.keywords[0].arg == "reason")


def _current_tests(ctx: Context, path: str) -> dict[str, _Test]:
    try:
        if not (ctx.root / path).resolve().is_relative_to(ctx.root.resolve()):
            return {}
    except (OSError, RuntimeError):
        return {}
    return _tests(ctx.read_text(path))


def _placeholder(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Assert)
        and isinstance(stmt.test, ast.Constant)
        and stmt.test.value is True
    )


def _static_truth(expr: ast.expr) -> bool | None:
    if isinstance(expr, ast.Constant):
        return bool(expr.value)
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        value = _static_truth(expr.operand)
        return None if value is None else not value
    if isinstance(expr, ast.BoolOp):
        decisive = isinstance(expr.op, ast.Or)
        unknown = False
        for operand in expr.values:
            value = _static_truth(operand)
            if value is decisive:
                return decisive
            unknown |= value is None
        return None if unknown else not decisive
    return None


def _has_live_name(expr: ast.expr) -> bool:
    pending: list[ast.AST] = [expr]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.BoolOp) and _static_truth(node) is not None:
            continue
        if isinstance(node, ast.IfExp):
            condition = _static_truth(node.test)
            if condition is not None:
                pending.append(node.body if condition else node.orelse)
                continue
        pending.extend(ast.iter_child_nodes(node))
    return False


def _prior_check(stmt: ast.stmt, equality: bool) -> str | None:
    if isinstance(stmt, ast.Assert) and _has_live_name(stmt.test):
        return "a nonconstant assertion"
    if equality and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        if (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "assertEqual"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            and len(call.args) == 2
            and not call.keywords
            and not any(isinstance(arg, ast.Starred) for arg in call.args)
            and any(_has_live_name(arg) for arg in call.args)
        ):
            return "a self.assertEqual call"
    return None


def _replacement(old: _Test, new: _Test) -> tuple[int, str] | None:
    if len(old.body) != len(new.body) or not any(map(_placeholder, new.body)):
        return None
    changed = None
    for index, (before, after) in enumerate(zip(old.body, new.body, strict=True)):
        try:
            if ast.dump(before) == ast.dump(after):
                continue
        except RecursionError:
            return None
        if changed is not None or not _placeholder(after):
            return None
        check = _prior_check(before, old.equality_method and new.equality_method)
        if check is None:
            return None
        changed = (index, check)
    return changed


def _lost_check_key(test: _Test, index: int) -> str:
    """Identify the old predicate and its occurrence among equivalent direct checks."""
    stmt = test.body[index]
    predicates = [
        ast.dump(node.test if isinstance(node, ast.Assert) else node.value)
        for node in test.body[: index + 1]
        if isinstance(node, (ast.Assert, ast.Expr)) and type(node) is type(stmt)
    ]
    predicate = predicates[-1]
    digest = sha256(predicate.encode()).hexdigest()
    return f"{type(stmt).__name__}:{digest}:{predicates.count(predicate)}"


def _gained_skip(prior: _Test, test: _Test) -> bool:
    if prior.skips or not test.skips:
        return False
    # A removed opaque decorator may already have applied the same skip.
    try:
        return Counter(map(ast.dump, prior.unknown_decorators)) <= Counter(
            map(ast.dump, test.unknown_decorators)
        )
    except RecursionError:
        return False


class TestWeakening(Rule):
    id = "test-weakening"
    summary = "Review unconditional pytest skips and direct placeholder assertions."
    default_enabled = False
    default_options: ClassVar = {"base": ""}
    severity = Severity.ADVICE

    def check(self, ctx: Context) -> Iterator[Finding]:
        if "base" in ctx.config.rules.get(self.id, {}) or self.options["base"]:
            base = git.comparison_base(ctx.root, self.options["base"])
            for old_path, path, blob in git.changed_blob_pairs(ctx.root, base):
                if not all(_test_path(p) and not ctx.is_excluded(p) for p in (old_path, path)):
                    continue
                current = _current_tests(ctx, path)
                if (
                    not current
                    or int(git.run_git(["cat-file", "-s", blob], ctx.root)) > MAX_TEXT_BYTES
                ):
                    continue
                data = git.run_git_bytes(["cat-file", "blob", blob], ctx.root)
                try:
                    previous = _tests(data.decode())
                except UnicodeDecodeError:
                    continue
                for name in current.keys() & previous.keys():
                    yield from self.observations(path, name, current[name], previous[name])
        else:
            for path in ctx.files:
                if _test_path(path):
                    for name, test in _current_tests(ctx, path).items():
                        yield from self.observations(path, name, test)

    def observations(
        self, path: str, name: str, test: _Test, prior: _Test | None = None
    ) -> Iterator[Finding]:
        if prior is None or _gained_skip(prior, test):
            verb = "gained" if prior else "has"
            for count, line in enumerate(test.skips, 1):
                yield self.finding(
                    f"{name} {verb} an unconditional pytest.mark.skip decorator.",
                    path=path,
                    line=line,
                    key=f"diff:{name}:skip:{count}" if prior else f"{name}:skip:{count}",
                )
        replaced = _replacement(prior, test) if prior else None
        count = 0
        for index, stmt in enumerate(test.body):
            if _placeholder(stmt):
                count += 1
                if prior is None or (replaced and index == replaced[0]):
                    message = "contains a direct 'assert True'"
                    key = f"{name}:assert:{count}"
                    if prior and replaced:
                        message = f"replaced {replaced[1]} with 'assert True'"
                        key = f"diff:{name}:assert:{_lost_check_key(prior, index)}"
                    yield self.finding(
                        f"{name} {message}.",
                        path=path,
                        line=stmt.lineno,
                        key=key,
                    )
