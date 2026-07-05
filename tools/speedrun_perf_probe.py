#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Cold-start + memory probe for a 50,000-card collection (AI-off, engine floor).

Complements tools/speedrun_latency_bench.py (which measures warm per-action
latency) by measuring the two spec section 10 targets it does not:

  * cold start: a FRESH process opening a pre-built 50k collection and doing the
    first dashboard load (readiness + coverage + performance), timed end to end;
  * memory: that same fresh process's peak RSS after the first dashboard load.

Both are ENGINE/BACKEND floors: they exclude Qt/Compose GUI startup and paint,
which a real app adds on top. The desktop app's own cold start is the Qt process
launch plus this engine open; the phone's is the Android process launch plus the
same engine via librsandroid.so.

Usage (via the built pylib bridge):
    PYTHONPATH=out/pylib:pylib out/pyenv/bin/python tools/speedrun_perf_probe.py [n_cards] [runs]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _rss_bytes_to_mb(maxrss: int) -> float:
    # macOS ru_maxrss is bytes; Linux is kilobytes.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return maxrss / divisor


# Child mode: open the given collection, run the first dashboard load, report
# elapsed seconds + peak RSS as JSON. Run in a fresh process for a true cold open.
def _child(path: str) -> int:
    import resource

    t0 = time.perf_counter()
    from anki import speedrun_pb2  # noqa: F401  (registers anki.speedrun_pb2)
    from anki.collection import Collection

    col = Collection(path)
    try:
        backend = col._backend
        backend.compute_readiness()
        backend.get_coverage_report()
        backend.get_performance_report()
        elapsed = time.perf_counter() - t0
    finally:
        col.close()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"elapsed_s": elapsed, "peak_rss_mb": _rss_bytes_to_mb(peak)}))
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--child":
        return _child(argv[1])

    n_cards = int(argv[0]) if argv else 50_000
    runs = int(argv[1]) if len(argv) > 1 else 3

    from speedrun_latency_bench import (  # type: ignore[import-not-found]
        build_synthetic_collection,
    )

    from anki.collection import Collection

    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)
    print(f"building a {n_cards:,}-card collection (setup, not timed)...")
    col = Collection(path)
    build_synthetic_collection(col, n_cards)
    col.close()  # persist to disk

    samples: list[dict] = []
    for i in range(runs):
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", path],
            capture_output=True,
            text=True,
            env=os.environ,
            check=False,
        )
        if out.returncode != 0:
            sys.stderr.write(out.stderr)
            return 1
        samples.append(json.loads(out.stdout.strip().splitlines()[-1]))

    cold = samples[0]  # first fresh process = coldest
    warm_after = samples[1:] or [cold]
    result = {
        "deck_size": n_cards,
        "runs": runs,
        "cold_start_open_plus_first_dashboard": {
            "cold_s": round(cold["elapsed_s"], 3),
            "subsequent_s": [round(s["elapsed_s"], 3) for s in warm_after],
            "budget_desktop_s": 5.0,
            "budget_phone_s": 4.0,
            "desktop_pass": cold["elapsed_s"] < 5.0,
        },
        "memory_50k": {
            "peak_rss_mb": round(cold["peak_rss_mb"], 1),
            "all_runs_mb": [round(s["peak_rss_mb"], 1) for s in samples],
        },
    }
    print(json.dumps(result, indent=2))

    os.unlink(path)
    # honest sanity: the engine cold open must clear the desktop budget on its own
    assert cold["elapsed_s"] < 5.0, cold
    print("\nperf-probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
