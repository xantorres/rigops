"""Plan files obey the grammar in plans/README.md.

Live plans are linted in full. ``archive/`` is a graveyard, not an instruction
surface, so it is checked only for the one thing that would be a lie there: a
plan still marked active.
"""

from __future__ import annotations

import re

from rigops import core

AREA = "plans"
CHECK = "plans"

EXEMPT = {"README.md", "backlog.md"}
FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.md$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = ("active", "done", "superseded", "abandoned")
ARCHIVED_STATUSES = ("done", "superseded", "abandoned")


def _frontmatter(path):
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    match = re.match(r"\A---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _plans_dir(cfg):
    return core.expand(core.cfg_get(cfg, "doctor.plans.dir", "~/.claude/plans"))


def _rel(path):
    return str(path).replace(str(core.HOME), "~")


def run(cfg):
    root = _plans_dir(cfg)
    if not root.is_dir():
        return []
    findings = []
    for path in sorted(root.glob("*.md")):
        if path.name in EXEMPT:
            continue
        where = _rel(path)
        if not FILENAME_RE.match(path.name):
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"name:{path.name}",
                symptom=f"plan filename `{path.name}` is not kebab-case",
                evidence=where, fix="rename to lowercase words joined by hyphens",
            ))
        fields = _frontmatter(path)
        missing = [k for k in ("status", "date", "topic") if not fields.get(k)]
        if missing:
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"frontmatter:{path.name}",
                symptom=f"plan `{path.name}` is missing frontmatter {', '.join(missing)}",
                evidence=where, fix="add the frontmatter block described in plans/README.md",
            ))
        status = fields.get("status", "")
        if status and status not in STATUSES:
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"status:{path.name}",
                symptom=(
                    f"plan `{path.name}` has status `{status}`, "
                    f"not one of {'/'.join(STATUSES)}"
                ),
                evidence=where, fix="use a status value from plans/README.md",
            ))
        date = fields.get("date", "")
        if date and not DATE_RE.match(date):
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"date:{path.name}",
                symptom=f"plan `{path.name}` has date `{date}`, not YYYY-MM-DD",
                evidence=where, fix="write the date the plan was authored as YYYY-MM-DD",
            ))
        if status in ARCHIVED_STATUSES:
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"unarchived:{path.name}",
                symptom=f"plan `{path.name}` is {status} but still sits outside archive/",
                evidence=where, fix=f"git mv it into {_rel(root)}/archive/",
            ))
    for path in sorted((root / "archive").glob("*.md")):
        if _frontmatter(path).get("status") == "active":
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"active-in-archive:{path.name}",
                symptom=f"archived plan `{path.name}` still claims status active",
                evidence=_rel(path), fix="move it back or settle its real status",
            ))
    return findings
