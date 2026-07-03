#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# One-command Speedrun gate: Rust tests + Python bridge tests + every tool
# self-test. Exits non-zero on the first failure.
#
#   ./tools/speedrun_check.sh
#
# Prereqs: protoc on PATH (or PROTOC set), and the pylib bridge built once
# (`./ninja pylib`).
set -euo pipefail

cd "$(dirname "$0")/.."

export PROTOC="${PROTOC:-$(command -v protoc || true)}"
if [ -z "$PROTOC" ]; then
    echo "protoc not found; install protobuf or set PROTOC" >&2
    exit 1
fi
if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

echo "== Rust tests (speedrun + points-at-stake) =="
cargo test --workspace --exclude rsbridge -- speedrun points_at_stake

echo
echo "== Python bridge tests =="
PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python -m pytest -p no:cacheprovider -q pylib/tests/test_speedrun.py

echo
echo "== Tool self-tests =="
./tools/speedrun_benchmark.sh >/dev/null && echo "benchmark .......... PASS"
./tools/import_question_pack.sh >/dev/null && echo "question-pack ...... PASS"
./tools/speedrun_ablation.sh >/dev/null && echo "ablation ........... PASS"
./tools/speedrun_e2e.sh >/dev/null && echo "e2e-workflow ....... PASS"
./tools/speedrun_e2e_full.sh >/dev/null && echo "e2e-full ........... PASS"

echo
echo "All Speedrun checks passed."
