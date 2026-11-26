"""Language-agnostic detection of comments on a single line.

Without parsing each language this is a heuristic tuned for precision:

- the comment syntax comes from the file type (suffix, a well-known name such
  as `Makefile`, or a `#!` line); files of other types, such as `.txt`, `.rst`
  or `.json`, have no comments;
- an opener counts only at the start of the line or after whitespace, so a
  Markdown heading `# TODO list` or a URL `http://host` is not a comment;
- text in quotes closed on the same line is skipped, so `echo "# TODO"` is
  not a comment either.

Python sources are tokenized instead.
"""

from __future__ import annotations

import re
import tokenize
from collections.abc import Iterator
from fnmatch import fnmatchcase
from functools import cache
from io import StringIO
from pathlib import PurePosixPath

PYTHON_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})

# "*" stands for the continuation line of a block comment (` * text`).
_HASH = ("#",)
_C = ("//", "/*", "*")
_MARKUP = ("<!--",)
_SYNTAX_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        _HASH,
        ".py .pyi .pyw .sh .bash .zsh .fish .ksh .rb .pl .pm .r .pyx .yaml .yml .toml .cfg .conf"
        " .properties .ps1 .psm1 .tf .tfvars .hcl .nix .jl .ex .exs .cmake .mk .cr .nim"
        " .coffee .awk .tcl .dockerfile",
    ),
    (("#", ";"), ".ini"),
    (
        _C,
        ".js .mjs .cjs .jsx .ts .mts .cts .tsx .java .kt .kts .scala .groovy .gradle .c .h"
        " .cc .cpp .cxx .hpp .hh .cs .go .rs .swift .dart .proto .jsonc .json5 .scss .less",
    ),
    (("/*", "*"), ".css"),
    (("//",), ".zig .sass"),
    (("//", "/*", "*", "#"), ".php"),
    (("<!--", "//", "/*", "*"), ".vue .svelte .astro"),
    (("--", "/*", "*"), ".sql"),
    (("--",), ".lua .hs .elm .adb .ads"),
    (("%",), ".tex .sty .cls .erl .hrl"),
    ((";",), ".lisp .el .clj .cljs .scm .rkt"),
    (("{#", "<!--"), ".j2 .jinja .jinja2 .twig .njk"),
    (_MARKUP, ".md .markdown .mdx .html .htm .xhtml .xml .svg"),
)
_SUFFIX_SYNTAX = {
    suffix: openers for openers, suffixes in _SYNTAX_GROUPS for suffix in suffixes.split()
}
# Lower-case file name patterns of files with `#` comments.
_HASH_NAMES = (
    "dockerfile dockerfile.* containerfile makefile gnumakefile justfile procfile vagrantfile"
    " gemfile rakefile podfile brewfile codeowners requirements*.txt constraints*.txt .env"
    " .env.* .envrc .*ignore .gitattributes .gitmodules .bashrc .bash_profile .zshrc .profile"
)
_HASH_PATTERNS = tuple(_HASH_NAMES.split())
# Quoted text closed on the same line; unclosed quotes (apostrophes) stay.
_QUOTED = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`""")


def comment_syntax(path: str, first_line: str = "") -> tuple[str, ...]:
    """Comment openers of a non-Python file; empty when its type is unknown."""
    file = PurePosixPath(path)
    syntax = _SUFFIX_SYNTAX.get(file.suffix.lower())
    if syntax is not None:
        return syntax
    name = file.name.lower()
    if any(fnmatchcase(name, pattern) for pattern in _HASH_PATTERNS):
        return _HASH
    return _HASH if first_line.startswith("#!") else ()


@cache
def _opener(openers: tuple[str, ...]) -> re.Pattern[str]:
    inline = "|".join(re.escape(o) for o in openers if o != "*")
    parts = [rf"(?:^|(?<=\s))(?:{inline})"] if inline else []
    if "*" in openers:
        parts.append(r"^\s*\*(?!/)")
    return re.compile("|".join(parts))


def find_comment(line: str, openers: tuple[str, ...]) -> str | None:
    """Text after the first comment opener outside quotes, or None if there is none."""
    if not openers:
        return None
    masked = line
    if not set(openers) <= set(_MARKUP):
        # Markup prose has apostrophes, not string literals.
        masked = _QUOTED.sub(lambda m: " " * len(m[0]), line)
    m = _opener(openers).search(masked)
    return None if m is None else line[m.end() :]


def comment_text(line: str, path: str) -> str | None:
    """Text after the first comment opener on the line, judged by the file type."""
    return find_comment(line, comment_syntax(path, line))


def iter_comments(source: str, path: str) -> Iterator[tuple[int, str, str]]:
    """Yield the line number, original line and comment body."""
    if PurePosixPath(path).suffix.lower() in PYTHON_SUFFIXES:
        reader = StringIO(source, newline=None).readline
        try:
            for token in tokenize.generate_tokens(reader):
                if token.type == tokenize.COMMENT:
                    yield token.start[0], token.line, token.string[1:]
        except (tokenize.TokenError, SyntaxError):
            # A work in progress can be incomplete. Only use recognized comments.
            return
        return
    openers = comment_syntax(path, source.partition("\n")[0])
    if not openers:
        return
    for number, line in enumerate(source.splitlines(), start=1):
        body = find_comment(line, openers)
        if body is not None:
            yield number, line, body
