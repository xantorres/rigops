"""The roots registry: which tree is which realm, and what a cwd may read.

Two questions, one file. For a path being indexed: which realm owns it, which
store is it, which repo does it belong to. For a working directory: which realm
am I in and which scopes may be prefetched here. An unmapped path is indexed
nowhere and an unmapped cwd prefetches nothing, because the failure that matters
is a wide default, not a narrow one.
"""

from __future__ import annotations

import functools
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from rigops import core

DEFAULT_REGISTRY = "~/rig/registry/roots.json"
ENCODE_RE = re.compile(r"[^A-Za-z0-9-]")


def _expand(value) -> Path:
    return Path(os.path.expanduser(str(value)))


def encode_project(path) -> str:
    """Mirror the harness's transcript directory naming for a cwd."""
    return ENCODE_RE.sub("-", str(path))


@functools.lru_cache(maxsize=512)
def _glob_to_re(pattern: str) -> "re.Pattern":
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches(rel: str, patterns) -> bool:
    for pattern in patterns:
        if _glob_to_re(pattern).match(rel):
            return True
        if not pattern.endswith("**") and _glob_to_re(pattern.rstrip("/") + "/**").match(rel):
            return True
    return False


@dataclass(frozen=True)
class Root:
    path: str
    realm: str
    scope: str
    repo: bool = False
    indexed: bool = True
    classify: bool = False
    include: tuple = ()
    exclude: tuple = ()
    extensions: tuple = ()
    sensitive: tuple = ()
    cwd_scopes: tuple = ()

    @property
    def is_glob(self) -> bool:
        return self.path.endswith("/*")

    def bases(self) -> list:
        """Concrete filesystem anchors this entry stands for."""
        if not self.is_glob:
            target = _expand(self.path)
            return [target] if target.exists() else []
        parent = _expand(self.path[:-2])
        if not parent.is_dir():
            return []
        # A dot-directory beside the repos is scratch, worktrees or tooling state,
        # never a repo, and indexing one would file its contents under its own name.
        return sorted(child for child in parent.iterdir()
                      if child.is_dir() and not child.name.startswith("."))


@dataclass(frozen=True)
class Profile:
    """What a working directory is allowed to see."""

    realm: str
    scopes: tuple
    repo: str = ""
    root: str = ""


@dataclass(frozen=True)
class Placement:
    """Where one indexed file sits."""

    realm: str
    scope: str
    repo: str
    root: Root
    base: Path
    rel: str
    extensions: tuple = ()
    sensitive: tuple = ()
    classify: bool = False


@dataclass
class Registry:
    raw: dict
    path: Path
    roots: list = field(default_factory=list)
    _anchors: list = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path=None) -> "Registry":
        target = _expand(path or os.environ.get("RIGOPS_ROOTS") or DEFAULT_REGISTRY)
        data = json.loads(target.read_text()) if target.is_file() else {}
        roots = []
        for entry in data.get("roots", []):
            roots.append(Root(
                path=entry.get("path", ""),
                realm=entry.get("realm", ""),
                scope=entry.get("scope", ""),
                repo=bool(entry.get("repo", False)),
                indexed=entry.get("index", True) is not False,
                classify=bool(entry.get("classify", False)),
                include=tuple(entry.get("include", ()) or ()),
                exclude=tuple(entry.get("exclude", ()) or ()),
                extensions=tuple(entry.get("extensions", ()) or ()),
                sensitive=tuple(entry.get("sensitive_name_parts", ()) or ()),
                cwd_scopes=tuple(entry.get("cwd_scopes", ()) or ()),
            ))
        return cls(raw=data, path=target, roots=roots)

    # ---- configuration ------------------------------------------------

    @property
    def realms(self) -> tuple:
        return tuple(self.raw.get("realms", ("client", "personal", "mixed")))

    @property
    def scopes(self) -> tuple:
        return tuple(self.raw.get("scopes", ()))

    @property
    def client_tokens(self) -> tuple:
        return tuple(t.lower() for t in self.raw.get("client_tokens", ()))

    def index_cfg(self, key, default=None):
        return self.raw.get("index", {}).get(key, default)

    @property
    def db_path(self) -> Path:
        return _expand(self.index_cfg("db", "~/.cache/docs-fts/index.sqlite"))

    # ---- indexing side ------------------------------------------------

    def anchors(self) -> list:
        """(Root, base) for every indexable anchor, longest base first.

        Memoised: `place` is called once per candidate file, and re-listing every
        root directory per file turned an incremental reindex into six minutes.
        """
        if self._anchors is not None:
            return self._anchors
        pairs = []
        for root in self.roots:
            if not root.indexed:
                continue
            for base in root.bases():
                pairs.append((root, base))
        self._anchors = sorted(pairs, key=lambda pair: len(str(pair[1])), reverse=True)
        return self._anchors

    def place(self, path) -> "Placement":
        """Placement for one file path, or None when no root owns it."""
        target = Path(path)
        for root, base in self.anchors():
            if base.is_file():
                if target != base:
                    continue
                rel = target.name
            else:
                try:
                    rel = str(target.relative_to(base))
                except ValueError:
                    continue
            if root.include and not _matches(rel, root.include):
                continue
            if root.exclude and _matches(rel, root.exclude):
                continue
            return Placement(
                realm=root.realm,
                scope=root.scope,
                repo=base.name if root.repo else "",
                root=root, base=base, rel=rel,
                extensions=root.extensions or tuple(self.index_cfg("extensions", (".md", ".txt"))),
                sensitive=root.sensitive or tuple(self.index_cfg("sensitive_name_parts", ())),
                classify=root.classify,
            )
        return None

    def realm_for_text(self, placement, text: str) -> str:
        """A personal file that names the client is neither, so it is `mixed`.

        Fails closed in both directions: `mixed` rows are returned to an explicit
        query and to no prefetch at all, so infrastructure notes that mention a
        client repo cannot ride into a personal turn, nor personal notes into a
        client one.
        """
        if not placement.classify or placement.realm != "personal":
            return placement.realm
        low = text.lower()
        return "mixed" if any(token in low for token in self.client_tokens) else "personal"

    # ---- memory directories -------------------------------------------

    def memory_dirs(self) -> list:
        """(dir, realm, repo) for every per-project memory directory we can place."""
        cfg = self.raw.get("memory_root", {})
        root_dir = _expand(cfg.get("path", "~/.claude/projects"))
        if not root_dir.is_dir():
            return []
        anchors = []
        for root in self.roots:
            for base in root.bases():
                anchors.append((encode_project(base), root, base))
        anchors.sort(key=lambda item: len(item[0]), reverse=True)
        out = []
        for project in sorted(root_dir.iterdir()):
            memory = project / "memory"
            if not memory.is_dir():
                continue
            name = project.name
            for encoded, root, base in anchors:
                if name == encoded or name.startswith(encoded + "-"):
                    out.append((memory, root.realm, base.name if root.repo else ""))
                    break
        return out

    def unmapped_memory_dirs(self) -> list:
        mapped = {str(item[0]) for item in self.memory_dirs()}
        cfg = self.raw.get("memory_root", {})
        root_dir = _expand(cfg.get("path", "~/.claude/projects"))
        if not root_dir.is_dir():
            return []
        out = []
        for project in sorted(root_dir.iterdir()):
            memory = project / "memory"
            if memory.is_dir() and str(memory) not in mapped and any(memory.iterdir()):
                out.append(memory)
        return out

    # ---- query side ----------------------------------------------------

    def profile(self, cwd) -> "Profile":
        """What the caller in `cwd` may prefetch, or None when nothing maps."""
        target = Path(os.path.abspath(os.path.expanduser(str(cwd))))
        best = None
        for root in self.roots:
            if not root.cwd_scopes:
                continue
            for base in root.bases():
                if target == base or base in target.parents:
                    if best is None or len(str(base)) > len(str(best[1])):
                        best = (root, base)
        if best is None:
            return None
        root, base = best
        return Profile(
            realm=root.realm,
            scopes=tuple(root.cwd_scopes),
            repo=base.name if root.repo else "",
            root=str(base),
        )

    # ---- validation -----------------------------------------------------

    def problems(self) -> list:
        """(key, symptom, evidence, fix) for every defect in the registry itself."""
        out = []
        if not self.path.is_file():
            return [("missing", f"roots registry {self.path} does not exist",
                     str(self.path), "create it, or point RIGOPS_ROOTS at one")]
        realms, scopes = set(self.realms), set(self.scopes)
        memory_scope = self.raw.get("memory_root", {}).get("scope", "memory")
        # Memory rows come from decoding a project directory back to a root, not
        # from a roots entry of their own, so the scope is produced by definition.
        produced = {memory_scope}
        for root in self.roots:
            where = f"{self.path}:{root.path}"
            if root.realm not in realms:
                out.append((f"realm:{root.path}", f"root `{root.path}` has realm `{root.realm}`",
                            where, f"use one of {'/'.join(sorted(realms))}"))
            if root.scope not in scopes:
                out.append((f"scope:{root.path}", f"root `{root.path}` has scope `{root.scope}`",
                            where, f"use one of {'/'.join(sorted(scopes))}"))
            if root.indexed:
                produced.add(root.scope)
            if not root.bases():
                out.append((f"absent:{root.path}", f"root `{root.path}` matches nothing on disk",
                            where, "remove the entry or restore the tree"))
            for scope in root.cwd_scopes:
                if scope not in scopes:
                    out.append((f"cwdscope:{root.path}:{scope}",
                                f"root `{root.path}` grants unknown scope `{scope}`",
                                where, f"use one of {'/'.join(sorted(scopes))}"))
        for root in self.roots:
            for scope in root.cwd_scopes:
                if scope in scopes and scope not in produced:
                    out.append((f"empty:{scope}",
                                f"scope `{scope}` is granted but no root indexes it",
                                f"{self.path}:{root.path}",
                                "index a root with that scope or drop it from cwd_scopes"))
        by_realm = {}
        for root in self.roots:
            if root.indexed:
                by_realm.setdefault(root.realm, set()).add(root.scope)
            # A memory directory is produced by decoding a project name back to a
            # root, so every realm with a root can produce memory rows.
            by_realm.setdefault(root.realm, set()).add(memory_scope)
        for root in self.roots:
            own = by_realm.get(root.realm, set())
            for scope in root.cwd_scopes:
                if scope in scopes and scope not in own:
                    out.append((
                        f"grant:{root.path}:{scope}",
                        f"`{root.path}` grants scope `{scope}` but no `{root.realm}` root"
                        " produces it, so the grant returns nothing or crosses realms",
                        f"{self.path}:{root.path}",
                        f"index a {root.realm} root with scope {scope}, or drop the grant",
                    ))
        if any(root.classify for root in self.roots) and not self.client_tokens:
            out.append(("tokens", "roots ask for token classification but the token list is empty",
                        str(self.path), "fill client_tokens, or drop classify"))
        seen = set()
        return [item for item in out if not (item[0] in seen or seen.add(item[0]))]


def load(path=None) -> Registry:
    return Registry.load(path)


def state_path(name: str) -> Path:
    """State lives outside `~/.claude` even when RIGOPS_STATE_DIR points inside it."""
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    path = _expand(Path(base) / "rigops" / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["Registry", "Root", "Profile", "Placement", "load", "encode_project",
           "state_path", "core"]
