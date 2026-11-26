"""Static observations about Python test definitions and their Git counterparts."""

from __future__ import annotations

import ast
import builtins
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
from pyhyi.rules.exception_checks import ExceptionCheck, exception_check
from pyhyi.rules.predicates import widening

Function = ast.FunctionDef | ast.AsyncFunctionDef
Imports = dict[str, tuple[str, int]]
_CASES = {"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"}
_EXCEPTIONS = {
    name
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
}
_UNIT_COMPARISONS = {
    "assertEqual": ast.Eq,
    "assertNotEqual": ast.NotEq,
    "assertIn": ast.In,
    "assertLess": ast.Lt,
    "assertLessEqual": ast.LtE,
    "assertGreater": ast.Gt,
    "assertGreaterEqual": ast.GtE,
}
_UNIT_METHODS = set(_UNIT_COMPARISONS) | {
    "assertIsNotNone",
    "assertTrue",
    "assertFalse",
    "assertRaises",
    "assertRaisesRegex",
}


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
                "pytest.raises",
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
    pending: list[tuple[ast.AST, bool]] = [(stmt, False) for stmt in reversed(body)]
    while pending:
        stmt, conditional = pending.pop()
        if isinstance(stmt, (ast.ClassDef, Function)):
            continue
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            # Compound statements, except handlers and match cases share this namespace.
            pending.extend(
                (child, True)
                for child in ast.iter_child_nodes(stmt)
                if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case))
            )
            continue
        for target in targets:
            owner, _, attr = _dotted_name(target).rpartition(".")
            if attr == "__test__":
                if isinstance(stmt.value, ast.Constant) and stmt.value.value is True:
                    if not conditional:
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
    signature: tuple[str, ...]
    methods: set[str]
    exceptions: dict[ast.stmt, ExceptionCheck]
    line: int


def _exception_contexts(
    func: Function, imports: Imports, hidden: set[str], methods: set[str]
) -> dict[ast.stmt, ExceptionCheck]:
    contexts = {}
    pytest_names = {key for key, (target, _) in imports.items() if target == "pytest"} - hidden
    raises_names = {
        key for key, (target, _) in imports.items() if target == "pytest.raises"
    } - hidden
    if not (pytest_names or raises_names or methods & {"assertRaises", "assertRaisesRegex"}):
        return contexts
    names = _EXCEPTIONS - hidden
    pending: list[ast.AST] = list(reversed(func.body))
    while pending:
        node = pending.pop()
        if isinstance(node, (Function, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.With, ast.Expr)):
            context = exception_check(
                node,
                pytest_names=pytest_names,
                raises_names=raises_names,
                builtin_names=names,
                unittest_methods=methods,
            )
            if context is not None:
                contexts[node] = context
        pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return contexts


def _methods(
    body: list[ast.stmt],
    scope: Counter[str],
    imports: Imports,
    module_names: set[str],
    *,
    decorator_imports: Imports | None = None,
    methods: set[str] | None = None,
) -> Iterator[tuple[str, _Test]]:
    if "*" in scope:
        return
    if decorator_imports is None:
        decorator_imports = imports
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
            target = _resolve(expr, decorator_imports)
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
            local = set(_bindings(func.body))
            local.update(n.arg for n in ast.walk(func.args) if isinstance(n, ast.arg))
            ordinary = (
                methods
                and not unknown
                and bool(args)
                and args[0].arg == "self"
                and "self" not in _bindings(func.body, count_mutations=False)
            )
            allowed = methods if ordinary else set()
            try:
                signature = tuple(ast.dump(n) for n in [func.args, *func.decorator_list])
            except RecursionError:
                continue
            # A module import is expected to bind its own alias. Other module
            # bindings can shadow built-in exception types.
            hidden = local | (module_names & _EXCEPTIONS)
            yield (
                func.name,
                _Test(
                    func.body,
                    skips,
                    unknown,
                    "assertEqual" in allowed,
                    signature,
                    allowed,
                    _exception_contexts(func, imports, hidden, allowed),
                    func.lineno,
                ),
            )


def _inherited_methods(cls: ast.ClassDef, scope: Counter[str]) -> set[str]:
    if "__getattribute__" in scope:
        return set()
    methods = _UNIT_METHODS.difference(scope)
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            methods.discard(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
        ):
            if not isinstance(node.args[1], ast.Constant):
                return set()
            methods.discard(node.args[1].value)
    return methods


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
    module_names = set(bindings)
    tests = dict(_methods(module.body, bindings, imports, module_names))
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
        try:
            class_signature = tuple(
                f"{field}={ast.dump(node)}"
                for field in ("decorator_list", "bases", "keywords")
                for node in getattr(cls, field)
            )
        except RecursionError:
            continue
        # Method decorators see class bindings. Method bodies resolve globals in the module.
        visible = {key: value for key, value in imports.items() if key not in scope}
        # An attribute assignment such as pt.raises = other can mutate the imported object.
        plain_bindings = _bindings(cls.body, count_mutations=False)
        body_imports = {k: v for k, v in imports.items() if scope[k] == plain_bindings[k]}
        known_class = all(
            _resolve(d.func if isinstance(d, ast.Call) else d, imports)
            in {"unittest.skip", "unittest.skipIf", "unittest.skipUnless"}
            for d in cls.decorator_list
        )
        methods = _inherited_methods(cls, scope) if case and known_class else set()
        for name, test in _methods(
            cls.body, scope, body_imports, module_names, decorator_imports=visible, methods=methods
        ):
            if name not in local_disabled:
                test.signature = class_signature + test.signature
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
        if isinstance(node, ast.BoolOp):
            # In a value context the selected operand itself is returned.
            decisive = isinstance(node.op, ast.Or)
            for operand in node.values:
                pending.append(operand)
                if _static_truth(operand) is decisive:
                    break
            continue
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and _static_truth(node) is not None
        ):
            continue
        if isinstance(node, ast.IfExp):
            condition = _static_truth(node.test)
            if condition is not None:
                pending.append(node.body if condition else node.orelse)
                continue
        pending.extend(ast.iter_child_nodes(node))
    return False


def _prior_check(stmt: ast.stmt, equality: bool) -> str | None:
    if (
        isinstance(stmt, ast.Assert)
        and _static_truth(stmt.test) is None
        and _has_live_name(stmt.test)
    ):
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


def _predicate_text(value: object) -> str:
    """Encode AST types and named fields consistently, including empty operand lists."""
    if isinstance(value, ast.AST):
        fields = (
            f"{name}={_predicate_text(child)}"
            for name, child in ast.iter_fields(value)
            if child is not None or (isinstance(value, ast.Constant) and name == "value")
        )
        return f"{type(value).__name__}({', '.join(fields)})"
    if isinstance(value, list):
        return "[" + ", ".join(map(_predicate_text, value)) + "]"
    return repr(value)


def _lost_check_key(test: _Test, index: int) -> str:
    """Identify the old predicate and its occurrence among equivalent direct checks."""
    stmt = test.body[index]
    predicates = [
        _predicate_text(node.test if isinstance(node, ast.Assert) else node.value)
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


def _predicate(stmt: ast.stmt, methods: set[str]) -> ast.expr | None:
    if isinstance(stmt, ast.Assert):
        return stmt.test
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not isinstance(call.func, ast.Attribute) or _dotted_name(call.func.value) != "self":
        return None
    method = call.func.attr
    if method not in methods or call.keywords or any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if method in _UNIT_COMPARISONS and len(call.args) == 2:
        return ast.Compare(call.args[0], [_UNIT_COMPARISONS[method]()], [call.args[1]])
    if len(call.args) == 1:
        value = call.args[0]
        if method == "assertIsNotNone":
            return ast.Compare(value, [ast.IsNot()], [ast.Constant(None)])
        if method == "assertTrue":
            return value
        if method == "assertFalse":
            return ast.UnaryOp(ast.Not(), value)
    return None


def _joint_guard(
    statements: list[ast.stmt], index: int, methods: set[str]
) -> tuple[ast.expr, tuple[int, ...]] | None:
    """Combine consecutive checks on related values before comparing their strength."""

    def names(expr: ast.expr) -> set[str]:
        return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}

    current = _predicate(statements[index], methods)
    assert current is not None
    block = {index: current}
    for direction in (-1, 1):
        i = index + direction
        while 0 <= i < len(statements):
            pred = _predicate(statements[i], methods)
            if pred is None:
                break
            block[i] = pred
            if len(block) > 32:
                return None
            i += direction
    related = {index}
    used = names(current)
    dependencies = {i: names(expr) for i, expr in block.items()}
    while extra := {i for i, deps in dependencies.items() if i not in related and used & deps}:
        related.update(extra)
        for i in extra:
            used.update(dependencies[i])
    indices = tuple(sorted(related))
    if len(indices) == 1:
        return current, indices
    return ast.BoolOp(ast.And(), [block[i] for i in indices]), indices


def _changed_checks(
    before: list[ast.stmt],
    after: list[ast.stmt],
    old: _Test,
    new: _Test,
    trail: str = "",
    *,
    assertions: bool = True,
) -> Iterator[tuple[str, ast.stmt, int, str]]:
    """Pair local edits only when surrounding setup and control flow agree."""
    left = [ast.dump(s) for s in before]
    right = [ast.dump(s) for s in after]
    if left == right:
        return
    if len(before) == len(after):
        position = {node: i for i, node in enumerate(before)}
        left_set, right_set = set(left), set(right)
        pairs = [(a, b) for a, b, x, y in zip(before, after, left, right, strict=True) if x != y]
        # Reordering assertions or rewriting setup makes positional pairing ambiguous.
        if len(pairs) > 1 and any(
            _predicate(a, old.methods) is None
            or _predicate(b, new.methods) is None
            or left[position[a]] in right_set
            or right[position[a]] in left_set
            for a, b in pairs
        ):
            return
        reported_groups = set()
        for a, b in pairs:
            if a in old.exceptions and b in new.exceptions:
                change = new.exceptions[b].weaker_than(old.exceptions[a])
                if change:
                    yield trail, a, new.exceptions[b].lineno, change
                # An assertion failure inside a raises context can make the outer test pass.
                continue
            first, second = _predicate(a, old.methods), _predicate(b, new.methods)
            if first is not None and second is not None and not _placeholder(b):
                if not assertions:
                    continue
                index = position[a]
                group_old = _joint_guard(before, index, old.methods)
                group_new = _joint_guard(after, index, new.methods)
                if group_old is None or group_new is None:
                    continue
                guard_old, indices_old = group_old
                guard_new, indices_new = group_new
                if indices_old != indices_new or indices_old in reported_groups:
                    continue
                witness = widening(guard_old, guard_new)
                if witness is not None:
                    reported_groups.add(indices_old)
                    yield (
                        trail,
                        a,
                        b.lineno,
                        (
                            f"changed '{ast.unparse(guard_old)}' to '{ast.unparse(guard_new)}'; "
                            f"the new check accepts {witness}, which the old check rejects."
                        ),
                    )
            elif (
                assertions
                and isinstance(b, ast.Pass)
                and first is not None
                and _has_live_name(first)
            ):
                yield trail, a, b.lineno, f"removed the check '{ast.unparse(first)}'."
            elif isinstance(
                a, (ast.With, ast.AsyncWith, ast.If, ast.For, ast.AsyncFor, ast.While)
            ) and type(a) is type(b):
                # Compare each suite under the same enclosing statement and unchanged header.
                header = {
                    k: _predicate_text(v)
                    for k, v in ast.iter_fields(a)
                    if k not in {"body", "orelse"}
                }
                if all(header[k] == _predicate_text(getattr(b, k)) for k in header):
                    prefix = f"{trail}/{type(a).__name__}:{header!r}"
                    for field in ("body", "orelse"):
                        yield from _changed_checks(
                            getattr(a, field, []),
                            getattr(b, field, []),
                            old,
                            new,
                            f"{prefix}/{field}",
                            assertions=assertions and not isinstance(a, (ast.With, ast.AsyncWith)),
                        )
        return
    if len(before) != len(after) + 1:
        return
    idx = next((i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b), len(after))
    if left[idx] in right or left[idx + 1 :] != right[idx:]:
        return
    stmt = before[idx]
    predicate = _predicate(stmt, old.methods)
    if (
        assertions
        and predicate is not None
        and _has_live_name(predicate)
        and _static_truth(predicate) is None
    ):
        line = after[min(idx, len(after) - 1)].lineno if after else new.line
        yield trail, stmt, line, f"removed the check '{ast.unparse(predicate)}' from this test."


class TestWeakening(Rule):
    id = "test-weakening"
    summary = "Review weaker assertions, exception expectations, removed checks and pytest skips."
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
        # Broad expectations in a snapshot are Ruff B017/PT011; only a comparison adds evidence.
        if prior is not None and test.signature == prior.signature:
            try:
                changes = list(_changed_checks(prior.body, test.body, prior, test))
            except RecursionError:
                changes = []
            for trail, stmt, line, change in changes:
                try:
                    key = sha256((trail + _predicate_text(stmt) + change).encode()).hexdigest()
                except RecursionError:
                    continue
                yield self.finding(
                    f"{name}: {change}", path=path, line=line, key=f"diff:{name}:weaker:{key}"
                )
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
