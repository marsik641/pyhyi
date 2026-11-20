"""Static observations about Python test definitions and their Git counterparts."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
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


def _bindings(body: list[ast.stmt], *, count_mutations: bool = True) -> Counter[str]:
    """Count potential bindings, including conditional ones, in this namespace."""
    names: Counter[str] = Counter()
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] += 1
            pending.extend(
                child for child in ast.iter_child_nodes(node) if not isinstance(child, ast.stmt)
            )
            continue
        if isinstance(node, ast.alias):
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
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                names[root.id] += 1
        pending.extend(ast.iter_child_nodes(node))
    return names


def _imports(module: ast.Module, bindings: Counter[str]) -> Imports:
    imports = {}
    for stmt in module.body:
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
            if bindings[name] == 1 and target in {"unittest", "pytest", "pytest.mark", *_CASES}:
                imports[name] = (target, stmt.lineno)
    return imports


def _resolve(expr: ast.expr, imports: Imports) -> str | None:
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name) and expr.id in imports:
        target, line = imports[expr.id]
        if line < expr.lineno:
            return ".".join([target, *reversed(parts)])
    return None


@dataclass
class _Test:
    body: list[ast.stmt]
    skips: list[int]
    possible_skip: bool
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
        possible_skip = False
        for decorator in func.decorator_list:
            expr = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(expr, ast.Attribute) and expr.attr == "skip":
                possible_skip = True
                if _resolve(expr, imports) == "pytest.mark.skip" and (
                    not isinstance(decorator, ast.Call) or _skip_arguments(decorator)
                ):
                    skips.append(decorator.lineno)
        args = func.args.posonlyargs + func.args.args
        ordinary = (
            equality
            and len(skips) == len(func.decorator_list)
            and bool(args)
            and args[0].arg == "self"
            and "self" not in _bindings(func.body, count_mutations=False)
        )
        yield (
            func.name,
            _Test(
                func.body,
                skips,
                possible_skip,
                ordinary,
            ),
        )


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
    bindings = _bindings(module.body)
    if "*" in bindings:
        return {}
    imports = _imports(module, bindings)
    tests = dict(_methods(module.body, bindings, imports))
    for cls in module.body:
        if not isinstance(cls, ast.ClassDef) or bindings[cls.name] != 1 or cls.keywords:
            continue
        case = len(cls.bases) == 1 and _resolve(cls.bases[0], imports) in _CASES
        if not case and (cls.bases or not cls.name.startswith("Test")):
            continue
        scope = _bindings(cls.body)
        if not case and {"__init__", "__new__"}.intersection(scope):
            continue
        visible = {key: value for key, value in imports.items() if key not in scope}
        equality = case and not cls.decorator_list and not _overrides_equality(cls, scope)
        for name, test in _methods(cls.body, scope, visible, equality=equality):
            tests[f"{cls.name}.{name}"] = test
    return tests


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


def _prior_check(stmt: ast.stmt, equality: bool) -> str | None:
    if isinstance(stmt, ast.Assert) and any(isinstance(n, ast.Name) for n in ast.walk(stmt.test)):
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
            and any(isinstance(n, ast.Name) for arg in call.args for n in ast.walk(arg))
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
                if not (_test_path(old_path) and _test_path(path)) or ctx.is_excluded(path):
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
        if prior is None or not prior.possible_skip:
            verb = "gained" if prior else "has"
            for count, line in enumerate(test.skips, 1):
                yield self.finding(
                    f"{name} {verb} an unconditional pytest.mark.skip decorator.",
                    path=path,
                    line=line,
                    key=f"{name}:skip:{count}",
                )
        replaced = _replacement(prior, test) if prior else None
        count = 0
        for index, stmt in enumerate(test.body):
            if _placeholder(stmt):
                count += 1
                if prior is None or (replaced and index == replaced[0]):
                    message = "contains a direct 'assert True'"
                    if replaced:
                        message = f"replaced {replaced[1]} with 'assert True'"
                    yield self.finding(
                        f"{name} {message}.",
                        path=path,
                        line=stmt.lineno,
                        key=f"{name}:assert:{count}",
                    )
