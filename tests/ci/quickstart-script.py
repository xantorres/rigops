"""Print the README quickstart's bash fences as one script that installs WHEEL, not the index."""
from __future__ import annotations

import re
import shlex
import sys

INSTALL_LINE = "pipx install rigops"


def main(readme: str, wheel: str) -> int:
    with open(readme, encoding="utf-8") as handle:
        text = handle.read()
    section = re.search(r"^## Quickstart\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if section is None:
        print("README has no '## Quickstart' section", file=sys.stderr)
        return 2
    lines = "".join(re.findall(r"^```bash\n(.*?)^```", section.group(1), re.S | re.M)).splitlines()
    if lines.count(INSTALL_LINE) != 1:
        print(f"the quickstart must hold exactly one `{INSTALL_LINE}` line", file=sys.stderr)
        return 2
    install = f"pipx install {shlex.quote(wheel)}"
    print("\n".join(install if line == INSTALL_LINE else line for line in lines))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: quickstart-script.py README WHEEL", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
