#!/bin/bash
# Builds the wheel of the previous release into OUTDIR and prints its path.
# The previous release is the nearest ancestor tag of HEAD^ that already shipped
# a pyproject.toml; with none, a copy of HEAD at version 0.0.0 stands in.
set -eu

if [ $# -ne 1 ]; then
    echo "usage: previous-wheel.sh OUTDIR" >&2
    exit 2
fi
mkdir -p "$1"
outdir="$(cd "$1" && pwd)"
repo="$(git rev-parse --show-toplevel)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

previous=""
ref="HEAD^"
while tag="$(git -C "$repo" describe --tags --abbrev=0 --match 'v*' "$ref" 2>/dev/null)"; do
    if git -C "$repo" cat-file -e "$tag:pyproject.toml" 2>/dev/null; then
        previous="$tag"
        break
    fi
    ref="$tag^"
done

mkdir "$work/src"
if [ -n "$previous" ]; then
    git -C "$repo" archive "$previous" | tar -x -C "$work/src"
    echo "building previous release $previous" >&2
else
    git -C "$repo" archive HEAD | tar -x -C "$work/src"
    sed 's/^__version__ = .*/__version__ = "0.0.0"/' "$work/src/lib/rigops/__init__.py" > "$work/init.py"
    mv "$work/init.py" "$work/src/lib/rigops/__init__.py"
    echo "no earlier package release; building a synthetic 0.0.0 from HEAD" >&2
fi

mkdir "$work/out"
python3 -m pip wheel --quiet --no-deps -w "$work/out" "$work/src" >&2
set -- "$work"/out/*.whl
mv "$1" "$outdir/"
echo "$outdir/$(basename "$1")"
