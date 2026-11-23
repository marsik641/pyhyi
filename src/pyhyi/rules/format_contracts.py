"""Check a narrow, statically recognizable display-precision contract."""

from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import PurePosixPath

from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.rules import Rule
from pyhyi.rules.contract_source import masked_js, sources

_JS = (".ts", ".js", ".mjs", ".mts")
_ID = r"[A-Za-z_$][\w$]*"
_NUMBER = r"(?:0|[1-9]\d{0,12})(?:\.\d{1,10})?"
_STEP_NAME = re.compile(r"(?:step|increment|quantum|granularity|resolution|tick|spacing)$", re.I)
_TYPE = r"(?:\??\s*:\s*(?:number|undefined|null)(?:\s*\|\s*(?:number|undefined|null))*)?"
_DECL = re.compile(
    rf"\b(?P<exported>export\s+)?(?:const\s+(?P<arrow>{_ID})\s*=|function\s+(?P<func>{_ID}))\s*"
    rf"\(\s*(?P<value>{_ID}){_TYPE}\s*,\s*(?P<step>{_ID}){_TYPE}\s*\)"
    r"(?:\s*:\s*string)?\s*(?:=>\s*)?(?:\{\s*)?(?:return\s+)?"
)
_FORMATTER = re.compile(
    rf"\bconst\s+({_ID})\s*=\s*new\s+Intl\.NumberFormat\s*\("
    r"\s*(?:undefined\s*)?,\s*\{([^{}]*)\}\s*\)"
)
_IMPORT = re.compile(r"\bimport\s*\{([^{}]*)\}\s*from\b")
_RELATIVE = re.compile(r"""\s*(['"])(\.[^'"\n]+)\1""")
_PROPERTIES = re.compile(rf"({_ID})\.({_ID})\s*,\s*\1\.({_ID})")
_LITERALS = re.compile(rf"(-?{_NUMBER})\s*,\s*({_NUMBER})")
_DECLARED = re.compile(rf"\b(?:const|let|var|function|class)\s+({_ID})")
_PARAMETERS = re.compile(r"\(([^(){}]*)\)\s*(?::\s*[\w\[\]| ]+)?\s*(?:=>|\{)")


@dataclass(frozen=True)
class Branch:
    path: str
    line: int
    name: str
    threshold: Decimal
    inclusive: bool
    digits: int
    fixed: bool
    exported: bool
    wrapper: str | None


def _ambiguous_names(code: str, imported: bool = False) -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    for declaration in _DECLARED.finditer(code):
        counts[declaration[1]] += 1
    names = {name for name, count in counts.items() if count > (0 if imported else 1)}
    for parameters in _PARAMETERS.finditer(code):
        for part in parameters[1].split(","):
            if argument := re.match(rf"\s*(?:\.\.\.)?({_ID})(?=\s*(?:[?=:]|$))", part):
                names.add(argument[1])
    names.update(re.findall(rf"(?<![\w$])({_ID})\s*=>", code))
    return names


def _branches(path: str, code: str) -> Iterator[Branch]:
    if "toFixed" not in code and "NumberFormat" not in code:
        return
    ambiguous = _ambiguous_names(code)
    digits_by_name = {}
    for item in _FORMATTER.finditer(code):
        if item[1] in ambiguous or code.count("{", 0, item.start()) != code.count(
            "}", 0, item.start()
        ):
            continue
        # Ignore significant digits, non-decimal styles and dynamic/spread options.
        digits = {}
        for part in item[2].strip().rstrip(",").split(","):
            option = re.fullmatch(r"\s*(m(?:inimum|aximum)FractionDigits)\s*:\s*(\d{1,2})\s*", part)
            if option is None or option[1] in digits:
                break
            digits[option[1]] = int(option[2])
        else:
            if "maximumFractionDigits" in digits and (
                digits.get("minimumFractionDigits", 0) <= digits["maximumFractionDigits"] <= 9
            ):
                digits_by_name[item[1]] = digits["maximumFractionDigits"]

    for decl in _DECL.finditer(code):
        name = decl["arrow"] or decl["func"]
        if name in ambiguous or code.count("{", 0, decl.start()) != code.count(
            "}", 0, decl.start()
        ):
            continue
        value, step = map(re.escape, (decl["value"], decl["step"]))
        if not _STEP_NAME.search(decl["step"]):
            continue
        # Only inspect the first return/guard. A later guard can narrow the accepted steps.
        arm = re.compile(
            rf"(?:{value}\s*===?\s*null\s*\?\s*:\s*)?"
            rf"(?:if\s*\(\s*)?(?:{step}\s*!==?\s*(?:undefined|null)\s*&&\s*)?"
            rf"{step}\s*(<=?)\s*({_NUMBER})\s*(?:\?\s*|\)\s*\{{?\s*return\s+)"
            rf"(?:(?P<wrapper>{_ID})\(\s*)?(?:(?P<intl>{_ID})\.format\(\s*{value}\s*\)"
            rf"|{value}\.toFixed\(\s*(?P<fixed>\d)\s*\))"
        ).match(code, decl.end())
        if not arm:
            continue
        digits = int(arm["fixed"]) if arm["fixed"] is not None else digits_by_name.get(arm["intl"])
        if digits is not None:
            yield Branch(
                path,
                code.count("\n", 0, decl.start()) + 1,
                name,
                Decimal(arm[2]),
                arm[1] == "<=",
                digits,
                arm["fixed"] is not None,
                decl["exported"] is not None,
                arm["wrapper"],
            )


def _number(raw: object) -> Decimal | None:
    if not isinstance(raw, (str, int, Decimal)):
        return None
    text = str(raw)
    return Decimal(text) if re.fullmatch("-?" + _NUMBER, text) else None


def _loss(branch: Branch, raw_value: object, raw_step: object) -> tuple[Decimal, Decimal] | None:
    value, step = _number(raw_value), _number(raw_step)
    if value is None or step is None or step <= 0 or step > branch.threshold:
        return None
    if step == branch.threshold and not branch.inclusive:
        return None
    if value % step:
        return None
    binary = float(value)
    if Decimal(str(binary)) != value:
        return None
    # toFixed rounds the actual binary64 value; Intl rounds its decimal representation.
    number = Decimal.from_float(binary) if branch.fixed else value
    rendered = number.quantize(Decimal(1).scaleb(-branch.digits), rounding=ROUND_HALF_UP)
    return (value, rendered) if value != rendered else None


def _records(node: object) -> Iterator[dict]:
    pending = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _imports(
    path: str,
    source: str,
    code: str,
    definitions: dict[tuple[str, str], Branch],
    paths: set[str],
) -> dict[str, Branch]:
    result = {}
    for statement in _IMPORT.finditer(code):
        module = _RELATIVE.match(source, statement.end())
        if not module:
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), module[2]))
        choices = [target] if PurePosixPath(target).suffix else [target + ext for ext in _JS]
        resolved = [path for path in choices if path in paths]
        if len(resolved) != 1:
            continue
        for specifier in statement[1].split(","):
            binding = re.fullmatch(rf"\s*({_ID})(?:\s+as\s+({_ID}))?\s*", specifier)
            if not binding:
                continue
            item = definitions.get((resolved[0], binding[1]))
            if item and item.exported:
                result[binding[2] or binding[1]] = item
    if result:
        for shadow in _ambiguous_names(code, imported=True):
            result.pop(shadow, None)
    return result


def _code(source: str, path: str) -> str:
    if not path.endswith(".vue"):
        return masked_js(source)
    # Only script blocks and interpolations contain JS here. Keep original source offsets.
    pieces = list(re.sub(r"[^\n]", " ", source))
    for match in re.finditer(
        r"<!--.*?-->|<script\b[^>]*>(.*?)</script>|\{\{(.*?)\}\}", source, re.S
    ):
        group = match.lastindex
        if group is not None:
            pieces[match.start(group) : match.end(group)] = masked_js(match[group])
    return "".join(pieces)


def observations(ctx: Context, rule: Rule) -> Iterator[Finding]:
    """Report a precision loss at a literal call or a matching repository-data example."""
    definitions = {}
    scripts = {}
    for path, source in sources(ctx, (*_JS, ".vue")):
        code = _code(source, path)
        scripts[path] = source, code
        for branch in _branches(path, code):
            definitions[path, branch.name] = branch
    if not definitions:
        return

    records = defaultdict(dict)
    evidence = {}
    paths = set(scripts)
    for path, (source, code) in scripts.items():
        scope = _imports(path, source, code, definitions, paths)
        scope.update({name: item for (p, name), item in definitions.items() if p == path})
        if not scope:
            continue
        calls = re.compile(r"(?<![\w$.])(" + "|".join(map(re.escape, scope)) + r")\s*\(([^()]*)\)")
        for call in calls.finditer(code):
            branch = scope[call[1]]
            location = f"{path}:{code.count(chr(10), 0, call.start()) + 1}"
            args = call[2].strip()
            literal = _LITERALS.fullmatch(args)
            if literal and (loss := _loss(branch, literal[1], literal[2])):
                evidence.setdefault(branch, (*loss, literal[2], f"Literal call at {location}."))
            fields = _PROPERTIES.fullmatch(args)
            if fields:
                records[fields[2], fields[3]].setdefault(branch, location)

    # A pair of matching property names is evidence for a review, not a data-flow proof.
    pending = {b for group in records.values() for b in group} - evidence.keys()
    if pending:
        for path, source in sources(ctx, (".json",)):
            try:
                data = json.loads(source, parse_float=Decimal)
            except (ValueError, RecursionError, InvalidOperation):
                continue
            for row in _records(data):
                for (value_key, step_key), group in records.items():
                    if value_key not in row or step_key not in row:
                        continue
                    for branch in pending.intersection(group):
                        if loss := _loss(branch, row[value_key], row[step_key]):
                            evidence[branch] = (
                                *loss,
                                str(row[step_key]),
                                f"Call at {group[branch]} reads {value_key} and {step_key}. "
                                f"The connection between a matching record in {path} "
                                "and this call is unverified.",
                            )
                            pending.remove(branch)
                if not pending:
                    break
            if not pending:
                break
    for branch, (value, rendered, step, detail) in evidence.items():
        if branch.wrapper:
            detail += f" This models the input to {branch.wrapper}. Its return value is unknown."
        yield rule.finding(
            f"For {value} at step {step}, the formatter inside {branch.name} "
            f"has a maximum fractional precision of {branch.digits} and would yield {rendered}.",
            path=branch.path,
            line=branch.line,
            key=f"format-precision:{branch.name}",
            hint=f"{detail} Check whether the display must preserve increments of {step}.",
        )
