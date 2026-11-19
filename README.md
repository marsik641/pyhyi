# pyhyi

Repository hygiene checker. It catches the mess that autonomous coding agents
(and hurried humans) leave behind: dump folders, stale docs, broken doc links,
forgotten TODOs, junk in the repo root, commits that ignore the convention.

> Status: pre-alpha. Nothing to check yet — the skeleton is being built.

## Idea

Strong models know the conventions. They just don't follow the implicit ones
unless someone enforces them. pyhyi turns those implicit expectations into
explicit, checkable rules, so a tool slaps the agent's hands instead of a human.

Planned design:

- **Language-agnostic core** — git history, file layout, text references between
  files. Works on any repository.
- **Thin language adapters** — precise dependency graphs where needed
  (Python and JS/TS first), built on existing tools rather than custom parsers.
- **Precision over recall** — every finding should be worth reading. Suppressions
  require a reason; a baseline lets legacy repos adopt it gradually.
- **Several entry points** — CLI (`pyhyi check`), pytest plugin, pre-commit hook.
- **Agent-friendly output** — JSON reports an agent can act on directly.

## Install

Not on PyPI yet. Install from git by tag:

```sh
pip install "git+https://github.com/marsik641/pyhyi@v0.0.1"
```

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## License

MIT
