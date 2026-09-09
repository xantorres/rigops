"""render_local: ~/rig/law.md -> ~/.config/ai-agent/system-prompt.md."""

from __future__ import annotations

from pathlib import Path

from .common import Rendered, join_blocks, lines_of, marker_md, section_lines


def render_local(source: Path, home: Path) -> Rendered:
    law_path = source / "law.md"
    if not law_path.is_file():
        return Rendered()

    text = law_path.read_text()
    lines = lines_of(text)
    law_section = section_lines(lines, "# Law", include_heading=True)
    voice_section = section_lines(lines, "## Voice", include_heading=True)

    content = marker_md(law_path, home) + "\n" + join_blocks(law_section, voice_section)
    target = home / ".config" / "ai-agent" / "system-prompt.md"
    return Rendered(files={target: content.encode()})
