"""root-clutter: only well-known files live in the repository root."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from fnmatch import fnmatchcase
from typing import Any, ClassVar

from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.rules import Rule

# Documentation and community files, matched by the name before the first dot
# (README.md, LICENSE, LICENSE-MIT, CHANGELOG.rst, ...).
DOC_STEMS = frozenset(
    {
        "readme",
        "license",
        "licence",
        "copying",
        "notice",
        "authors",
        "contributors",
        "maintainers",
        "codeowners",
        "changelog",
        "changes",
        "history",
        "news",
        "contributing",
        "code_of_conduct",
        "security",
        "support",
        "governance",
        "citation",
        "architecture",
        "roadmap",
        "agents",
        "claude",
        "gemini",
    }
)
DOC_PREFIXES = ("license-", "licence-", "copying-")

# Manifests, lockfiles, build and tool configuration, matched case-insensitively.
KNOWN_FILES = (
    # VCS and editors
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".mailmap",
    ".git-blame-ignore-revs",
    ".editorconfig",
    ".pre-commit-config.yaml",
    ".pre-commit-hooks.yaml",
    ".markdownlint.json",
    ".markdownlint.jsonc",
    "*.code-workspace",
    # generic config shapes
    "*.toml",
    "*.yml",
    "*.yaml",
    "*.lock",
    "*.ini",
    "*.cfg",
    ".*rc",
    ".*rc.*",
    "*.config",
    "*.config.*",
    ".*ignore",
    # containers and task runners
    "dockerfile",
    "dockerfile.*",
    "*.dockerfile",
    "containerfile",
    "makefile",
    "gnumakefile",
    "*.mk",
    "justfile",
    "procfile",
    "vagrantfile",
    # Python
    "requirements*.txt",
    "constraints*.txt",
    "manifest.in",
    "pipfile",
    ".python-version",
    ".coveragerc",
    ".flake8",
    "py.typed",
    # JavaScript / TypeScript
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "bun.lockb",
    "deno.json",
    "deno.jsonc",
    "tsconfig*.json",
    "jsconfig*.json",
    "biome.json",
    "biome.jsonc",
    "components.json",
    "turbo.json",
    "nx.json",
    "lerna.json",
    "vercel.json",
    "firebase.json",
    "renovate.json",
    "app.json",
    ".node-version",
    "index.html",
    # Conventional database schema, distinct from arbitrary SQL dumps.
    "schema.sql",
    # other ecosystems
    "go.mod",
    "go.sum",
    "go.work",
    "go.work.sum",
    "cargo.toml",
    "gemfile",
    "rakefile",
    "*.gemspec",
    "composer.json",
    "pom.xml",
    "mvnw",
    "mvnw.cmd",
    "build.gradle*",
    "settings.gradle*",
    "gradlew",
    "gradlew.bat",
    "build.sbt",
    "cmakelists.txt",
    "*.sln",
    "*.csproj",
    "*.cabal",
    "mix.exs",
    "package.swift",
    "flake.nix",
    "default.nix",
    "shell.nix",
    "build",
    "build.bazel",
    "workspace",
    "module.bazel",
    ".tool-versions",
    ".ruby-version",
    ".nvmrc",
    # environment templates (a real .env must not be committed)
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    # pyhyi itself
    ".pyhyi-baseline.json",
)

# Source files are not clutter: small projects legitimately keep code in the root.
SOURCE_SUFFIXES = frozenset(
    {
        "py",
        "pyi",
        "ipynb",
        "js",
        "mjs",
        "cjs",
        "jsx",
        "ts",
        "mts",
        "cts",
        "tsx",
        "vue",
        "svelte",
        "go",
        "rs",
        "rb",
        "php",
        "java",
        "kt",
        "kts",
        "scala",
        "swift",
        "c",
        "h",
        "cc",
        "cpp",
        "hpp",
        "cs",
        "fs",
        "ex",
        "exs",
        "erl",
        "hs",
        "lua",
        "pl",
        "pm",
        "r",
        "jl",
        "dart",
        "nim",
        "zig",
        "sh",
        "bash",
        "zsh",
        "fish",
        "ps1",
        "bat",
        "cmd",
    }
)


def is_known_root_file(name: str, extra: tuple[str, ...] = ()) -> bool:
    lower = name.lower()
    stem = lower.split(".", 1)[0]
    if stem in DOC_STEMS or lower.startswith(DOC_PREFIXES):
        return True
    if "." in lower.lstrip(".") and lower.rsplit(".", 1)[1] in SOURCE_SUFFIXES:
        return True
    return any(fnmatchcase(lower, pattern.lower()) for pattern in (*KNOWN_FILES, *extra))


class RootClutter(Rule):
    id = "root-clutter"
    summary = "The repository root holds only well-known project files and source code."
    default_options: ClassVar[Mapping[str, Any]] = {"allow": []}

    def check(self, ctx: Context) -> Iterator[Finding]:
        extra = tuple(self.options["allow"])
        for path in ctx.files:
            if "/" in path or is_known_root_file(path, extra):
                continue
            yield self.finding(
                f"Unexpected file in the repository root: {path}",
                path=path,
                hint=(
                    "Move it into a directory (docs/, scripts/, data/, ...), delete it, "
                    f'or allow it: [tool.pyhyi.rules.{self.id}] allow = ["{path}"].'
                ),
            )
