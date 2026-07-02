# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Desktop adapter for the optional AI diagnosis coach.

The coach itself (tools/speedrun_ai) is provider-agnostic Python that depends on
`openai`, which is installed only in the isolated eval venv (anki/out/ai-venv) —
not in Anki's runtime. So we run it as a short-lived subprocess with that venv's
Python, off the UI thread, and degrade silently to the deterministic classifier
whenever the venv/key is missing or anything errors. AI is enrichment, never a
dependency: the reviewer/practice flow is fully functional with this off.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any, Callable

_CFG_AI_DIAGNOSIS = "speedrunAiDiagnosis"

_ANKI_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TOOLS = _ANKI_ROOT / "tools"
_VENV_PY = _ANKI_ROOT / "out" / "ai-venv" / "bin" / "python"


def enabled(col: Any) -> bool:
    try:
        return bool(col.get_config(_CFG_AI_DIAGNOSIS, False))
    except Exception:
        return False


def available() -> bool:
    """True when the coach can actually run (venv + coach package present)."""
    return _VENV_PY.exists() and (_TOOLS / "speedrun_ai" / "coach.py").exists()


def _run(payload: dict) -> dict | None:
    try:
        proc = subprocess.run(
            [str(_VENV_PY), "-m", "speedrun_ai.diagnose_cli"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(_TOOLS),
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


def _future_result(fut: Any) -> dict | None:
    try:
        return fut.result()
    except Exception:
        return None


def diagnose_in_background(
    mw: Any,
    item: dict,
    signals: dict,
    on_result: Callable[[dict | None], None],
) -> None:
    """Run the coach off the UI thread; deliver the result dict (or None) on the
    main thread. Returns None immediately when AI is off/unavailable."""
    if mw is None or mw.col is None or not enabled(mw.col) or not available():
        on_result(None)
        return
    payload = {"item": item, "signals": signals}
    mw.taskman.run_in_background(
        lambda: _run(payload),
        lambda fut: on_result(_future_result(fut)),
    )
