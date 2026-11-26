"""Static exception expectations in individual Python test statements."""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass

_ATOM = re.compile(
    r"(?P<atom>[^\\.^$*+?{}\[\]()|]|\.|\\[dws.$^*+?{}\[\]()|\\]|\[0-9\]|\[a-z\]|\[A-Z\])"
    r"(?P<repeat>[?*+]?)"
)
_EXAMPLES = {".": "x", r"\d": "0", r"\w": "x", r"\s": " ", "[0-9]": "0", "[a-z]": "a", "[A-Z]": "A"}


def _message_example(pattern: str, *, matching: bool) -> str | None:
    """Construct a witness for a concatenation of simple atoms with optional anchors."""
    if len(pattern) > 256:
        return None
    pos = int(pattern.startswith("^"))
    required = []
    while pos < len(pattern):
        if pos == len(pattern) - 1 and pattern[pos] == "$":
            break
        token = _ATOM.match(pattern, pos)
        if token is None:
            return None
        if token["repeat"] not in {"?", "*"}:
            atom = token["atom"]
            required.append(_EXAMPLES.get(atom, atom.removeprefix("\\")))
        pos = token.end()
    if matching:
        return "".join(required)
    if required:
        return ""
    return "x" if pattern == "^$" else None


def _has_example(expected: type[BaseException], message: str) -> bool:
    # Some builtins format their arguments specially or need several arguments.
    for args in ((message,), ()):
        try:
            error = expected(*args)
            if type(error) is expected and str(error) == message:
                return True
        except (TypeError, ValueError):
            continue
    return False


@dataclass(frozen=True)
class ExceptionCheck:
    lineno: int
    framework: str
    expected: type[BaseException]
    pattern: str | None
    context: tuple[str, ...]

    def weaker_than(self, old: ExceptionCheck) -> str | None:
        """Describe an outcome admitted by this expectation and rejected by the old one."""
        if self.framework != old.framework or self.context != old.context:
            return None
        if not issubclass(old.expected, self.expected):
            return None
        old_type, new_type = old.expected.__name__, self.expected.__name__
        if old.pattern != self.pattern and self.pattern:
            return None
        if old.expected is not self.expected:
            example = f"an exception whose exact type is {new_type}"
            if self.pattern:
                message = _message_example(self.pattern, matching=True)
                if message is None or not _has_example(self.expected, message):
                    return None
                example += f" with message {message!r}"
            change = f"broadened the expected type from {old_type} to {new_type}"
            if old.pattern and not self.pattern:
                change += f" and removed the message pattern {old.pattern!r}"
            return f"{change}, admitting {example}"
        if old.pattern and not self.pattern:
            message = _message_example(old.pattern, matching=False)
            if message is not None and _has_example(self.expected, message):
                return (
                    f"removed the message pattern {old.pattern!r}, admitting "
                    f"a {new_type} with message {message!r}"
                )
        return None


def _method(
    call: ast.Call, pytest_names: set[str], raises_names: set[str], unittest_methods: set[str]
) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in raises_names:
        return "pytest.raises"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in pytest_names and func.attr == "raises":
            return "pytest.raises"
        if (
            func.value.id == "self"
            and func.attr in {"assertRaises", "assertRaisesRegex"}
            and func.attr in unittest_methods
        ):
            return func.attr
    return None


def exception_check(
    stmt: ast.stmt,
    *,
    pytest_names: set[str],
    raises_names: set[str],
    builtin_names: set[str],
    unittest_methods: set[str],
) -> ExceptionCheck | None:
    """Inspect one with statement or direct callable assertion using caller-resolved names."""
    parts: list[ast.AST | str] = []
    if isinstance(stmt, ast.With):
        candidates = []
        for item in stmt.items:
            expr = item.context_expr
            method = (
                _method(expr, pytest_names, raises_names, unittest_methods)
                if isinstance(expr, ast.Call)
                else None
            )
            if method:
                candidates.append((expr, method))
                parts.append("expectation")
                if item.optional_vars is not None:
                    parts.append(item.optional_vars)
            else:
                parts.append(item)
        if len(candidates) != 1:
            return None
        call, method = candidates[0]
        parts.append("body")
        parts.extend(stmt.body)
    elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        method = _method(call, pytest_names, raises_names, unittest_methods)
        if not method:
            return None
        parts.append("callable")
    else:
        return None

    if not call.args or not isinstance(call.args[0], ast.Name):
        return None
    name = call.args[0].id
    expected = getattr(builtins, name, None) if name in builtin_names else None
    if not isinstance(expected, type) or not issubclass(expected, BaseException):
        return None
    args = call.args[1:]
    pattern = None
    if method == "assertRaisesRegex":
        if not args or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
            return None
        pattern = args[0].value
        args = args[1:]
    if isinstance(stmt, ast.With):
        if args:
            return None
        for keyword in call.keywords:
            if method == "pytest.raises" and keyword.arg == "match":
                if not isinstance(keyword.value, ast.Constant):
                    return None
                pattern = keyword.value.value
                if pattern is not None and not isinstance(pattern, str):
                    return None
            elif method != "pytest.raises" and keyword.arg == "msg":
                parts.append(keyword)
            else:
                return None
    else:
        # In callable form, keywords (including pytest's "match") go to the callable.
        if not args or not isinstance(args[0], (ast.Name, ast.Attribute, ast.Lambda)):
            return None
        parts.extend(args)
        parts.extend(call.keywords)
    framework = "pytest" if method == "pytest.raises" else "unittest"
    try:
        context = tuple(part if isinstance(part, str) else ast.dump(part) for part in parts)
    except RecursionError:
        return None
    return ExceptionCheck(call.lineno, framework, expected, pattern, context)
