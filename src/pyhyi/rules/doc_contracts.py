"""Find quoted source declarations that no longer match their implementation."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache

from markdown_it import MarkdownIt

from pyhyi import git
from pyhyi.context import MAX_TEXT_BYTES, Context
from pyhyi.errors import GitError
from pyhyi.references import SOURCE_SUFFIXES
from pyhyi.rules.links import MARKDOWN_SUFFIXES, local_path

_MARKDOWN = MarkdownIt("commonmark")
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NAMED_BINDINGS = (*_FUNCTIONS, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)


@dataclass(frozen=True)
class DocumentationDrift:
    document: str
    line: int
    target: str
    name: str
    documented: str
    current: str


def _parse_python(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _declaration(node: ast.stmt) -> tuple[str, str] | None:
    if isinstance(node, _FUNCTIONS) and not node.decorator_list:
        kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        if getattr(node, "type_params", ()):
            return None
        try:
            parameters = ast.unparse(node.args)
            returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        except RecursionError:
            return None
        return node.name, f"{kind} {node.name}({parameters}){returns}"

    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, ast.AnnAssign):
        target, value = node.target, node.value
    else:
        return None
    if not isinstance(target, ast.Name) or not target.id.isupper():
        return None
    if (
        isinstance(value, ast.UnaryOp)
        and isinstance(value.op, (ast.UAdd, ast.USub))
        and isinstance(value.operand, ast.Constant)
        and type(value.operand.value) in (int, float)
    ):
        return target.id, f"{target.id} = {ast.literal_eval(value)!r}"
    if isinstance(value, ast.Constant) and type(value.value) in (int, float, bool, str, type(None)):
        return target.id, f"{target.id} = {value.value!r}"
    return None


def _bound_names(tree: ast.Module) -> Iterator[str]:
    # Count possible rebindings too, including conditional assignments. Local
    # shadowing can make a match inconclusive, which is safe to skip.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            yield node.id
        elif isinstance(node, _NAMED_BINDINGS):
            if node.name is not None:
                yield node.name
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            yield node.rest
        elif isinstance(node, ast.alias):
            yield node.asname or node.name.split(".", 1)[0]


def _source_declarations(ctx: Context, revision: str, path: str) -> dict[str, str]:
    blob = f"{revision}:{path}"
    try:
        if int(git.run_git(["cat-file", "-s", blob], ctx.root)) > MAX_TEXT_BYTES:
            return {}
        text = git.run_git_bytes(["cat-file", "blob", blob], ctx.root).decode("utf-8-sig")
    except (GitError, UnicodeDecodeError):
        return {}
    tree = _parse_python(text)
    if tree is None:
        return {}
    counts = Counter(_bound_names(tree))
    if "*" in counts:
        return {}
    declarations = {}
    for node in tree.body:
        if parsed := _declaration(node):
            name, declaration = parsed
            if counts[name] == 1:
                declarations[name] = declaration
    return declarations


def _quoted_declaration(text: str) -> tuple[str, str] | None:
    if text.startswith(("def ", "async def ")):
        text = text.removesuffix(":") + ":\n    pass"
    elif "=" not in text:
        return None
    tree = _parse_python(text)
    if tree is None or len(tree.body) != 1:
        return None
    return _declaration(tree.body[0])


def _claims(text: str, document: str) -> Iterator[tuple[str, str, str, int]]:
    for block in _MARKDOWN.parse(text):
        if block.type != "inline" or not block.children or not block.map:
            continue
        targets = {
            path
            for token in block.children
            if token.type == "link_open"
            and (path := local_path(str(token.attrGet("href") or ""), document))
            and path.endswith(SOURCE_SUFFIXES)
        }
        if len(targets) != 1:
            continue
        target = targets.pop()
        if not target.endswith(".py"):
            continue
        for token in block.children:
            if token.type == "code_inline" and (claim := _quoted_declaration(token.content)):
                name, declaration = claim
                yield target, name, declaration, block.map[0] + 1


def documentation_drift(ctx: Context) -> Iterator[DocumentationDrift]:
    if not git.has_commits(ctx.root):
        return
    if git.run_git(["rev-parse", "--is-shallow-repository"], ctx.root).strip() == "true":
        return

    @cache
    def declarations(revision: str, path: str) -> dict[str, str]:
        return _source_declarations(ctx, revision, path)

    clean_files = set(ctx.files) - ctx.changed
    for document in ctx.files:
        if document not in clean_files or not document.lower().endswith(MARKDOWN_SUFFIXES):
            continue
        claims: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
        for target, name, declaration, line in _claims(ctx.read_text(document) or "", document):
            if target in clean_files:
                claims[target, name].setdefault(declaration, line)
        if not claims:
            continue
        revision = git.run_git(
            ["--literal-pathspecs", "log", "-1", "--format=%H", "HEAD", "--", document],
            ctx.root,
        ).strip()
        if not revision:
            continue
        for (target, name), quoted in claims.items():
            if len(quoted) != 1:
                continue
            documented, line = next(iter(quoted.items()))
            current = declarations("HEAD", target).get(name)
            if current is None or current == documented:
                continue
            if declarations(revision, target).get(name) == documented:
                yield DocumentationDrift(document, line, target, name, documented, current)
