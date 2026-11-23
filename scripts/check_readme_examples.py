"""Run the marked README examples with commands from an installed wheel."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path
from tempfile import TemporaryDirectory

from markdown_it import MarkdownIt

EXPECTED_OUTPUT = {
    "check": "No issues found.",
    "json": None,
    "rules": "broken-doc-link",
    "pytest": "1 passed",
}


def examples(readme: Path) -> dict[str, tuple[int, list[str]]]:
    result = {}
    for token in MarkdownIt("commonmark").parse(readme.read_text(encoding="utf-8")):
        if token.type != "fence":
            continue
        tags = token.info.split()
        if len(tags) != 2 or tags[0] != "sh" or not tags[1].startswith("ci="):
            continue
        name = tags[1].removeprefix("ci=")
        line = token.map[0] + 2
        if name not in EXPECTED_OUTPUT or name in result:
            raise ValueError(f"{readme}:{line}: unknown or repeated example {name!r}")
        commands = [
            args
            for entry in token.content.splitlines()
            if (args := shlex.split(entry, comments=True))
        ]
        if len(commands) != 1 or commands[0][0] not in {"pyhyi", "pytest"}:
            raise ValueError(f"{readme}:{line}: expected one pyhyi or pytest command")
        result[name] = (line, commands[0])
    if missing := EXPECTED_OUTPUT.keys() - result.keys():
        raise ValueError(f"{readme}: missing marked examples: {', '.join(sorted(missing))}")
    return result


def verify(name: str, stdout: str) -> bool:
    if name == "json":
        return json.loads(stdout)["summary"]["issues"] == 0
    if name == "pytest":
        return "1 passed" in stdout and "No issues found." in stdout
    return EXPECTED_OUTPUT[name] in stdout


def main(readme: Path) -> None:
    try:
        commands = examples(readme)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    scripts = Path(sysconfig.get_path("scripts"))
    suffix = ".exe" if os.name == "nt" else ""
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        env.pop(key, None)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    with TemporaryDirectory(prefix="pyhyi-readme-") as tmp:
        root = Path(tmp)
        (root / "test_example.py").write_text("def test_example():\n    assert 2 + 2 == 4\n")
        subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True)
        for name, (line, argv) in commands.items():
            print(f"{readme}:{line} $ {shlex.join(argv)}", flush=True)
            result = subprocess.run(
                [str(scripts / (argv[0] + suffix)), *argv[1:]],
                cwd=root,
                env=env,
                capture_output=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            if result.returncode:
                print(result.stdout + result.stderr, file=sys.stderr)
                raise SystemExit(f"{readme}:{line}: command exited with {result.returncode}")
            try:
                matched = verify(name, result.stdout)
            except (KeyError, ValueError, TypeError):
                matched = False
            if not matched:
                print(result.stdout + result.stderr, file=sys.stderr)
                raise SystemExit(f"{readme}:{line}: unexpected output for {name!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
