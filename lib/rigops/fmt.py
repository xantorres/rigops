from __future__ import annotations

_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values) -> str:
    values = list(values)
    if not values:
        return ""
    numeric = [v for v in values if v is not None]
    if not numeric:
        return "·" * len(values)
    lo = min(numeric)
    hi = max(numeric)
    span = hi - lo
    chars = []
    for v in values:
        if v is None:
            chars.append("·")
            continue
        if span == 0:
            chars.append(_BLOCKS[0])
            continue
        idx = int((v - lo) / span * (len(_BLOCKS) - 1))
        idx = max(0, min(len(_BLOCKS) - 1, idx))
        chars.append(_BLOCKS[idx])
    return "".join(chars)


def human_bytes(n) -> str:
    if n < 1024:
        return f"{n} B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    return f"{mb:.1f} MB"


def table(rows, headers) -> str:
    headers = list(headers)
    str_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells):
        parts = []
        for i, cell in enumerate(cells):
            if i == len(cells) - 1:
                parts.append(cell)
            else:
                parts.append(cell.ljust(widths[i]))
        return "  ".join(parts)

    lines = [fmt_row(headers), "  ".join("-" * w for w in widths)]
    for row in str_rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)
