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

DEFAULT_DIR = "~/.claude/plans"
EXEMPT = {"README.md", "backlog.md"}
FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.md$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = ("active", "done", "superseded", "abandoned")
ARCHIVED_STATUSES = ("done", "superseded", "abandoned")


def _frontmatter(path):
    try:
        text = core.read_text(path)
    except OSError:
        return {}
    match = re.match(r"\A---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = _scalar(value)
    return fields


def _scalar(value):
    """A quoted or commented value is the same value: `status: "done"` is done.

    Comparing the raw text called a well-formed plan malformed and, worse, let a
    finished plan that quotes its status sit outside the archive unreported.
    """
    value = value.strip()
    if value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        return value[1:closing] if closing > 0 else value[1:]
    return value.split(" #", 1)[0].strip()


def _plans_dir(cfg):
    """The directory to lint, and whether an operator named it.

    A configured root that does not exist is a defect: a typo in the setting
    would otherwise disable the check silently. The default root is absent on any
    rig that keeps no plans, which is not a defect at all.
    """
    configured = core.cfg_get(cfg, "doctor.plans.dir")
    return core.expand(configured or DEFAULT_DIR), bool(configured)


def _rel(path):
    return str(path).replace(str(core.HOME), "~")


def run(cfg):
    root, configured = _plans_dir(cfg)
    if not root.is_dir():
        if not configured:
            return []
        return [core.Finding(
            check=CHECK, area=AREA, key=f"dir:{root}",
            symptom="configured plans directory does not exist, so no plan is linted",
            evidence=f"doctor.plans.dir = {_rel(root)}",
            fix="point doctor.plans.dir at the plans directory or drop the setting",
        )]
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
