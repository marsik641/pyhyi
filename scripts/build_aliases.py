"""Build alias distributions that install pyhyi under other names.

Each alias is a metadata-only package that depends on the exact same pyhyi
version, so `pip install <alias>` installs pyhyi and nothing else.

Usage: python scripts/build_aliases.py [--out-dir dist]
Each alias lands in its own subdirectory: dist/<alias>/.
Requires `uv` on PATH.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALIASES = ("pyhui", "pyhuy", "pihui", "pihuy", "pyhuj", "pohuj")

PYPROJECT = """\
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "{version}"
description = "Alias of pyhyi, the repository hygiene checker. Installs pyhyi."
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
requires-python = ">=3.10"
dependencies = ["pyhyi=={version}"]

[project.urls]
Repository = "https://github.com/marsik641/pyhyi"
Homepage = "https://pypi.org/project/pyhyi/"

[tool.hatch.build.targets.wheel]
bypass-selection = true
"""

README = """\
# {name}

`{name}` is an alias of [pyhyi](https://pypi.org/project/pyhyi/), a repository
hygiene checker. Installing it installs `pyhyi` {version}; use the `pyhyi`
command:

```sh
pip install {name}
pyhyi check
```

Prefer depending on `pyhyi` directly.
"""


def read_version() -> str:
    text = (ROOT / "src" / "pyhyi" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SystemExit("cannot find __version__ in src/pyhyi/__init__.py")
    return match[1]


def write_alias(name: str, version: str, parent: Path) -> Path:
    """Write the source tree of one alias package and return its directory."""
    src = parent / name
    src.mkdir(parents=True)
    for filename, template in (("pyproject.toml", PYPROJECT), ("README.md", README)):
        (src / filename).write_text(
            template.format(name=name, version=version), encoding="utf-8", newline="\n"
        )
    shutil.copyfile(ROOT / "LICENSE", src / "LICENSE")
    return src


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default="dist", help="output directory (default: dist)")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    version = read_version()
    with tempfile.TemporaryDirectory() as tmp:
        for name in ALIASES:
            src = write_alias(name, version, Path(tmp))
            subprocess.run(
                ["uv", "build", "--quiet", "--out-dir", str(out_dir / name), str(src)],
                check=True,
            )
            print(f"built {name} {version} -> {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
