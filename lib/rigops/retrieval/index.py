"""Walk every root, cut every file into sections, write one FTS row each.

The realm is decided here, from the registry, and stored on the row. Nothing
downstream can widen it: a query filters on a column that was written before the
question existed. A file no root claims is not indexed at all.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from rigops import core

from . import chunk
from . import roots as roots_mod

PATH_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")

SCHEMA = 3
COLUMNS = ("path", "mtime", "content", "realm", "scope", "repo", "title", "section",
           "line", "verified", "pathwords")
CREATE_SQL = """
CREATE VIRTUAL TABLE docs USING fts5(
    path UNINDEXED,
    mtime UNINDEXED,
    content,
    realm UNINDEXED,
    scope UNINDEXED,
    repo UNINDEXED,
    title,
    section,
    line UNINDEXED,
    verified UNINDEXED,
    pathwords,
    tokenize='porter unicode61 remove_diacritics 2'
)
"""
INSERT_SQL = f"INSERT INTO docs({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})"


def _meta(conn, key, default=None):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return row[0] if row else default


def _set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))


def schema_ok(db_path) -> bool:
    path = Path(db_path)
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        return str(_meta(conn, "schema")) == str(SCHEMA)
    finally:
        conn.close()


def _is_sensitive(name: str, parts) -> bool:
    low = name.lower()
    return any(part in low for part in parts)


def _walk_targets(root, base):
    """Only the subtrees an include pattern can match.

    A client repository is tens of gigabytes and contributes two files and a
    `.claude` directory; walking it whole to find them cost minutes per
    incremental reindex. The static prefix of each include pattern is the
    subtree that can possibly match, so that is what gets walked.
    """
    if not root.include:
        return [base]
    targets = []
    for pattern in root.include:
        static = pattern.split("*")[0].rstrip("/")
        target = (base / static) if static else base
        if target == base:
            return [base]
        if target not in targets:
            targets.append(target)
    return targets


def _candidates(registry):
    """Yield every file path under an indexable anchor, deduplicated."""
    skip = set(registry.index_cfg("skip_dirs", ()))
    seen = set()
    for root, base in registry.anchors():
        if base.is_file():
            if str(base) not in seen:
                seen.add(str(base))
                yield base
            continue
        for target in _walk_targets(root, base):
            if target.is_file():
                if str(target) not in seen:
                    seen.add(str(target))
                    yield target
                continue
            if not target.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = [d for d in dirnames if d not in skip]
                for name in filenames:
                    path = Path(dirpath) / name
                    if str(path) in seen:
                        continue
                    seen.add(str(path))
                    yield path


def _read(path, max_bytes):
    try:
        stat = path.stat()
    except OSError:
        return None, None
    if stat.st_size == 0 or stat.st_size > max_bytes:
        return None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(), int(stat.st_mtime)
    except OSError:
        return None, None


def rows_for(registry, path, placement, max_bytes, max_chars):
    """Every FTS row for one file, or an empty list when it is not indexable."""
    if placement is None:
        return []
    if path.suffix.lower() not in placement.extensions:
        return []
    if _is_sensitive(path.name, placement.sensitive):
        return []
    text, mtime = _read(path, max_bytes)
    if not text or not text.strip():
        return []
    realm = registry.realm_for_text(placement, text)
    title = chunk.title_of(text, path.stem)
    # Half of what a reader asks is where a thing lives, and that answer is in the
    # path, the store and the realm, none of which appear in the prose.
    words = " ".join(w for w in PATH_SPLIT_RE.split(str(path)) if len(w) > 1)
    pathwords = f"{words} {realm} {placement.scope} {placement.repo}".strip()
    rows = []
    for section in chunk.split(text, max_chars):
        rows.append((
            str(path), mtime, section.text, realm, placement.scope, placement.repo,
            title, section.heading, section.line, section.verified, pathwords,
        ))
    return rows


def _memory_placements(registry):
    """Per-project memory files, placed by decoding the project directory name."""
    extensions = tuple(registry.index_cfg("extensions", (".md", ".txt")))
    sensitive = tuple(registry.index_cfg("sensitive_name_parts", ()))
    out = []
    for memory, realm, repo in registry.memory_dirs():
        for path in sorted(memory.rglob("*")):
            if not path.is_file():
                continue
            out.append((path, roots_mod.Placement(
                realm=realm, scope="memory", repo=repo, root=None, base=memory,
                rel=path.name, extensions=extensions, sensitive=sensitive, classify=False,
            )))
    return out


def walk(registry):
    """(path, placement) for everything the registry claims, memory included."""
    for path in _candidates(registry):
        placement = registry.place(path)
        if placement is not None:
            yield path, placement
    for path, placement in _memory_placements(registry):
        yield path, placement


def build(registry, db_path=None, progress=None) -> dict:
    """Rebuild the whole index into a temporary file, then swap it in."""
    db_path = Path(db_path or registry.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(db_path.suffix + f".tmp.{os.getpid()}")
    if tmp_path.exists():
        tmp_path.unlink()
    max_bytes = registry.index_cfg("max_file_bytes", 10 * 1024 * 1024)
    max_chars = registry.index_cfg("max_section_chars", chunk.DEFAULT_MAX_CHARS)

    started = time.time()
    conn = sqlite3.connect(str(tmp_path))
    conn.execute("PRAGMA journal_mode=MEMORY;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute(CREATE_SQL)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")

    files = sections = 0
    per_realm = {}
    for path, placement in walk(registry):
        rows = rows_for(registry, path, placement, max_bytes, max_chars)
        if not rows:
            continue
        conn.executemany(INSERT_SQL, rows)
        files += 1
        sections += len(rows)
        per_realm[rows[0][3]] = per_realm.get(rows[0][3], 0) + len(rows)
        if progress and files % 500 == 0:
            progress(f"  indexed {files} files, {sections} sections")

    _set_meta(conn, "schema", SCHEMA)
    _set_meta(conn, "built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    _set_meta(conn, "registry", str(registry.path))
    _set_meta(conn, "realms", json.dumps(per_realm, sort_keys=True))
    conn.commit()
    conn.execute("INSERT INTO docs(docs) VALUES('optimize')")
    conn.commit()
    conn.close()

    if db_path.exists():
        db_path.unlink()
    tmp_path.rename(db_path)
    return {
        "files": files, "sections": sections, "realms": per_realm,
        "seconds": round(time.time() - started, 1),
        "db": str(db_path), "mb": round(db_path.stat().st_size / 1024 / 1024, 1),
    }


def update(registry, db_path=None, progress=None) -> dict:
    """Re-index only what changed. Falls back to a build on a schema mismatch."""
    db_path = Path(db_path or registry.db_path)
    if not schema_ok(db_path):
        return build(registry, db_path, progress)
    max_bytes = registry.index_cfg("max_file_bytes", 10 * 1024 * 1024)
    max_chars = registry.index_cfg("max_section_chars", chunk.DEFAULT_MAX_CHARS)

    started = time.time()
    conn = sqlite3.connect(str(db_path))
    known = {
        row[0]: row[1]
        for row in conn.execute("SELECT path, MAX(mtime) FROM docs GROUP BY path")
    }
    added = changed = removed = 0
    seen = set()
    for path, placement in walk(registry):
        key = str(path)
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            continue
        seen.add(key)
        if key in known and known[key] >= mtime:
            continue
        rows = rows_for(registry, path, placement, max_bytes, max_chars)
        if key in known:
            conn.execute("DELETE FROM docs WHERE path = ?", (key,))
            if rows:
                changed += 1
            else:
                removed += 1
        elif rows:
            added += 1
        if rows:
            conn.executemany(INSERT_SQL, rows)
    for key in known:
        if key not in seen:
            conn.execute("DELETE FROM docs WHERE path = ?", (key,))
            removed += 1
    _set_meta(conn, "built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    conn.commit()
    conn.close()
    return {
        "added": added, "changed": changed, "removed": removed,
        "seconds": round(time.time() - started, 1), "db": str(db_path),
    }


def stats(db_path=None, registry=None) -> dict:
    """Row counts per realm and scope, for the doctor check and the CLI."""
    registry = registry or roots_mod.load()
    db_path = Path(db_path or registry.db_path)
    if not db_path.exists():
        return {}
    if not schema_ok(db_path):
        return {"schema": "stale", "rows": None, "by": {},
                "note": f"{db_path} predates schema {SCHEMA}; run rigops retrieval build"}
    conn = sqlite3.connect(str(db_path))
    try:
        out = {"schema": _meta(conn, "schema"), "built_at": _meta(conn, "built_at"), "by": {}}
        for realm, scope, count in conn.execute(
            "SELECT realm, scope, count(*) FROM docs GROUP BY realm, scope ORDER BY realm, scope"
        ):
            out["by"][f"{realm}/{scope}"] = count
        out["rows"] = sum(out["by"].values())
        return out
    finally:
        conn.close()


def problems(registry, db_path=None) -> list:
    """(key, symptom, evidence, fix) for every defect in the index itself."""
    db_path = Path(db_path or registry.db_path)
    if not db_path.exists():
        return [("index:missing",
                 "the retrieval index does not exist, so every query falls back to a raw scan",
                 str(db_path), "run rigops retrieval build")]
    if not schema_ok(db_path):
        return [("index:schema", f"the retrieval index predates schema {SCHEMA}",
                 str(db_path), "run rigops retrieval build")]
    out = []
    realms, scopes = set(registry.realms), set(registry.scopes)
    counts = stats(db_path, registry).get("by", {})
    if not counts:
        return [("index:empty", "the retrieval index holds no rows",
                 str(db_path), "run rigops retrieval build")]
    for key, count in counts.items():
        realm, _, scope = key.partition("/")
        if realm not in realms:
            out.append((f"index:realm:{realm}",
                        f"{count} indexed rows carry realm `{realm}`, undefined in the registry",
                        str(db_path), "fix the roots registry, then rebuild the index"))
        if scope not in scopes:
            out.append((f"index:scope:{scope}",
                        f"{count} indexed rows carry scope `{scope}`, undefined in the registry",
                        str(db_path), "fix the roots registry, then rebuild the index"))
    return out


__all__ = ["build", "update", "stats", "problems", "walk", "rows_for", "schema_ok",
           "SCHEMA", "core"]
