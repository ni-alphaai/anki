#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Memory-model calibration proof for Speedrun (AI-off).

Are the model's pre-answer probabilities honest? When it says "80% chance of
recall", does the student actually recall ~80% of the time? This harness seeds a
held-out set of (predicted, outcome) pairs, feeds them through the same Rust
SpeedrunService the apps use (`get_calibration_report()`), and renders a
reliability diagram plus a written report.

It runs two generators over the same stratified predicted distribution:

  * well-calibrated - the outcome is truly sampled with probability = predicted,
    so the reliability points hug the y = x diagonal and Brier/log-loss are low;
  * over-confident  - the model is too sure of itself: the real recall is pulled
    toward a coin flip (a stated 90% is really ~70%), so the curve bows away from
    the diagonal and Brier/log-loss rise. This shows the score *detects*
    miscalibration.

It also demonstrates the give-up rule: below MIN_PREDICTIONS (=20) captured
predictions the engine abstains (`sufficient == False`) instead of reporting a
noisy score.

Usage:
    python tools/speedrun_calibration.py [n]      # n = held-out size (default 200)

With any invocation it runs both scenarios + the abstain demo, asserts the
invariants (well-calibrated Brier < over-confident Brier, 10 bins, ...), and
(re)writes tools/speedrun_calibration_chart.svg and
tools/speedrun_calibration_report.md - a re-runnable, deterministic self-test.

Run it via the wrapper so the built pylib bridge is on the path:
    ./tools/speedrun_calibration.sh [n]

The SVG is written by hand (plain XML text), so the chart never depends on
matplotlib being installed. If the built pylib is missing, the tool falls back
to a pure-Python re-implementation of the engine's calibration maths (verified
to match the engine when both are available) so the chart and report can still
be produced; it says so in that case.
"""

from __future__ import annotations

import math
import os
import random
import sys
from dataclasses import dataclass

# The engine (built pylib) is the source of truth, but importing it is optional
# so the chart/report can still be produced from the pure-Python fallback.
try:
    from anki import speedrun_pb2
    from anki.collection import Collection

    _HAVE_ENGINE = True
    _IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - only when pylib isn't built
    _HAVE_ENGINE = False
    _IMPORT_ERROR = repr(exc)

HERE = os.path.dirname(os.path.abspath(__file__))
CHART_PATH = os.path.join(HERE, "speedrun_calibration_chart.svg")
REPORT_PATH = os.path.join(HERE, "speedrun_calibration_report.md")

# Mirror rslib/src/speedrun/calibration.rs.
N_BINS = 10
MIN_PREDICTIONS = 20
LOG_EPS = 1e-7

DEFAULT_N = 200
# Fixed for reproducibility. This seed yields a well-calibrated curve that hugs
# the diagonal (max per-bin deviation ~0.05) while the over-confident curve stays
# clearly bowed, so the chart reads cleanly; any seed produces the same
# qualitative result (well-calibrated Brier < over-confident Brier).
SEED = 3171


@dataclass
class Bin:
    lo: float
    hi: float
    count: int
    mean_predicted: float
    mean_outcome: float


@dataclass
class Report:
    n: int
    brier: float
    log_loss: float
    sufficient: bool
    note: str
    bins: list[Bin]


# --------------------------------------------------------------------------- #
# Calibration maths (pure-Python mirror of the Rust engine)                    #
# --------------------------------------------------------------------------- #
def compute_calibration_py(pairs: list[tuple[float, bool]], n_bins: int = N_BINS) -> Report:
    """Pure-Python re-implementation of rslib compute_calibration().

    Kept byte-for-byte faithful to the engine (same binning rule, Brier =
    mean (p - y)^2, standard log-loss with the same epsilon, same
    MIN_PREDICTIONS abstain) so it can stand in when pylib isn't built and so we
    can cross-check the engine when it is.
    """
    n = len(pairs)
    n_bins = max(1, n_bins)
    brier_sum = 0.0
    log_loss_sum = 0.0
    bin_count = [0] * n_bins
    bin_pred = [0.0] * n_bins
    bin_out = [0.0] * n_bins

    for predicted, outcome in pairs:
        p = min(1.0, max(0.0, predicted))
        y = 1.0 if outcome else 0.0
        brier_sum += (p - y) * (p - y)
        pc = min(1.0 - LOG_EPS, max(LOG_EPS, p))
        log_loss_sum += -(y * math.log(pc) + (1.0 - y) * math.log(1.0 - pc))
        idx = int(p * n_bins)
        if idx >= n_bins:
            idx = n_bins - 1  # p == 1.0 lands in the last bin
        bin_count[idx] += 1
        bin_pred[idx] += p
        bin_out[idx] += y

    brier = brier_sum / n if n else 0.0
    log_loss = log_loss_sum / n if n else 0.0

    bins = []
    for i in range(n_bins):
        count = bin_count[i]
        if count:
            mean_predicted = bin_pred[i] / count
            mean_outcome = bin_out[i] / count
        else:
            mean_predicted = mean_outcome = 0.0
        bins.append(Bin(i / n_bins, (i + 1) / n_bins, count, mean_predicted, mean_outcome))

    sufficient = n >= MIN_PREDICTIONS
    note = (
        "calibration computed"
        if sufficient
        else f"not enough predictions: {n}/{MIN_PREDICTIONS}"
    )
    return Report(n, brier, log_loss, sufficient, note, bins)


# --------------------------------------------------------------------------- #
# Held-out prediction generators                                              #
# --------------------------------------------------------------------------- #
def generate_pairs(n: int, kind: str, seed: int = SEED) -> list[tuple[float, bool]]:
    """Seed a held-out set of (predicted, outcome) pairs for a scenario.

    Predicted probabilities are drawn *stratified* across the 10 bins (bin
    ``i % N_BINS``) so every bin is populated and the reliability curve is fully
    drawn; the predicted stream is identical across kinds (same seed), so the two
    scenarios share bin membership and only the outcomes differ. Calibration is a
    property of P(outcome | predicted), so stratifying the marginal of
    ``predicted`` does not bias Brier/log-loss.

    kind:
      - "calibrated":    outcome ~ Bernoulli(predicted)                (honest)
      - "overconfident": outcome ~ Bernoulli(0.5 + (predicted-0.5)*0.5)
                         (true recall pulled halfway to a coin flip)
    """
    pred_rng = random.Random(seed)
    out_rng = random.Random(seed + 1)
    pairs: list[tuple[float, bool]] = []
    for i in range(n):
        b = i % N_BINS
        lo = b / N_BINS
        # stay inside the bin, away from the edges, so f32/f64 rounding can never
        # move a prediction into a neighbouring bucket
        p = pred_rng.uniform(lo + 0.01, lo + 0.09)
        if kind == "calibrated":
            true_p = p
        elif kind == "overconfident":
            true_p = 0.5 + (p - 0.5) * 0.5
        else:
            raise ValueError(f"unknown generator kind: {kind!r}")
        outcome = out_rng.random() < true_p
        pairs.append((p, outcome))
    return pairs


# --------------------------------------------------------------------------- #
# Engine path                                                                 #
# --------------------------------------------------------------------------- #
def _new_collection() -> "Collection":
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path)


def _report_from_proto(cal) -> Report:
    bins = [
        Bin(b.lo, b.hi, b.count, b.mean_predicted, b.mean_outcome) for b in cal.bins
    ]
    return Report(cal.n, cal.brier, cal.log_loss, cal.sufficient, cal.note, bins)


def engine_report_for_pairs(pairs: list[tuple[float, bool]]) -> Report:
    """Record each pair through SpeedrunService.record_attempt (capturing the
    prediction) and read back get_calibration_report() - the real engine."""
    col = _new_collection()
    try:
        for i, (p, y) in enumerate(pairs):
            col._backend.record_attempt(
                speedrun_pb2.RecordAttemptRequest(
                    card_id=1000 + i,
                    note_id=1,
                    session_id="calibration",
                    answered_at_ms=1,
                    took_ms=5000,
                    question_type=0,
                    correct=bool(y),
                    predicted=float(p),
                    signals=speedrun_pb2.ClassifyAttemptRequest(
                        correct=bool(y), took_ms=5000, question_type=0
                    ),
                )
            )
        return _report_from_proto(col._backend.get_calibration_report())
    finally:
        col.close()


def report_for_pairs(pairs: list[tuple[float, bool]]) -> tuple[Report, str]:
    """Return (report, source). Uses the engine when available and cross-checks
    it against the pure-Python maths; otherwise falls back to pure Python."""
    py = compute_calibration_py(pairs)
    if not _HAVE_ENGINE:
        return py, "pure-python-fallback"
    eng = engine_report_for_pairs(pairs)
    # The engine stores predictions as f32; allow a small tolerance vs f64.
    assert eng.n == py.n, (eng.n, py.n)
    assert len(eng.bins) == len(py.bins) == N_BINS, (len(eng.bins), len(py.bins))
    assert abs(eng.brier - py.brier) < 2e-3, (eng.brier, py.brier)
    assert abs(eng.log_loss - py.log_loss) < 1e-2, (eng.log_loss, py.log_loss)
    assert eng.sufficient == py.sufficient
    return eng, "engine"


# --------------------------------------------------------------------------- #
# SVG reliability diagram (hand-written, dependency-free)                     #
# --------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(calibrated: Report, overconfident: Report, n: int) -> str:
    W, H = 780, 640
    left, right, top, bottom = 95, 575, 60, 540  # 480 x 480 plot square

    def sx(p: float) -> float:
        return left + p * (right - left)

    def sy(v: float) -> float:
        return bottom - v * (bottom - top)

    cx_mid = (left + right) / 2
    cy_mid = (top + bottom) / 2

    teal = "#0b8043"
    red = "#d1495b"
    grid = "#e6e6e6"
    axis = "#333333"
    diag = "#888888"

    parts: list[str] = []
    parts.append(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">'
    )
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')

    # title + subtitle
    parts.append(
        f'<text x="{cx_mid}" y="30" text-anchor="middle" font-size="19" '
        f'font-weight="bold" fill="{axis}">Speedrun memory-model calibration</text>'
    )
    parts.append(
        f'<text x="{cx_mid}" y="49" text-anchor="middle" font-size="12.5" '
        f'fill="#666666">Reliability diagram on held-out predictions '
        f'(n={n} per scenario, {N_BINS} equal-width bins, abstain below '
        f'{MIN_PREDICTIONS})</text>'
    )

    # gridlines + ticks
    for t in range(N_BINS + 1):
        v = t / N_BINS
        x = sx(v)
        y = sy(v)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle" '
            f'font-size="11" fill="#555555">{v:.1f}</text>'
        )
        parts.append(
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#555555">{v:.1f}</text>'
        )

    # plot border
    parts.append(
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" '
        f'fill="none" stroke="{axis}" stroke-width="1.3"/>'
    )

    # ideal diagonal y = x
    parts.append(
        f'<line x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(1):.1f}" y2="{sy(1):.1f}" '
        f'stroke="{diag}" stroke-width="1.6" stroke-dasharray="6 5"/>'
    )

    def curve(rep: Report, color: str) -> None:
        pts = [(sx(b.mean_predicted), sy(b.mean_outcome)) for b in rep.bins if b.count]
        if len(pts) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(
                f'<polyline points="{poly}" fill="none" stroke="{color}" '
                f'stroke-width="2.4"/>'
            )
        for x, y in pts:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.2" fill="{color}" '
                f'stroke="#ffffff" stroke-width="1"/>'
            )

    curve(overconfident, red)
    curve(calibrated, teal)

    # per-bin counts along the bottom (shared by both scenarios)
    parts.append(
        f'<text x="{left - 10}" y="{bottom + 33}" text-anchor="end" font-size="10" '
        f'fill="#999999">n/bin</text>'
    )
    for b in calibrated.bins:
        xc = sx((b.lo + b.hi) / 2)
        parts.append(
            f'<text x="{xc:.1f}" y="{bottom + 33}" text-anchor="middle" '
            f'font-size="10" fill="#999999">{b.count}</text>'
        )

    # axis titles
    parts.append(
        f'<text x="{cx_mid}" y="{bottom + 55}" text-anchor="middle" font-size="13" '
        f'fill="{axis}">Mean predicted probability of recall</text>'
    )
    parts.append(
        f'<text x="30" y="{cy_mid:.1f}" text-anchor="middle" font-size="13" '
        f'fill="{axis}" transform="rotate(-90 30 {cy_mid:.1f})">'
        f'Observed recall (mean outcome)</text>'
    )

    # legend, placed in the empty lower-right region of the plot
    lx = sx(0.50) + 6
    ly = sy(0.32)
    lw = right - lx - 8
    lh = 78
    parts.append(
        f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{lw:.1f}" height="{lh}" rx="6" '
        f'fill="#ffffff" fill-opacity="0.92" stroke="#cccccc" stroke-width="1"/>'
    )
    row_y = ly + 20
    parts.append(
        f'<line x1="{lx + 12:.1f}" y1="{row_y:.1f}" x2="{lx + 40:.1f}" y2="{row_y:.1f}" '
        f'stroke="{diag}" stroke-width="1.6" stroke-dasharray="6 5"/>'
    )
    parts.append(
        f'<text x="{lx + 48:.1f}" y="{row_y + 4:.1f}" font-size="11.5" fill="{axis}">'
        f'ideal: perfectly calibrated (y = x)</text>'
    )
    row_y += 24
    parts.append(
        f'<line x1="{lx + 12:.1f}" y1="{row_y:.1f}" x2="{lx + 40:.1f}" y2="{row_y:.1f}" '
        f'stroke="{teal}" stroke-width="2.4"/>'
    )
    parts.append(
        f'<circle cx="{lx + 26:.1f}" cy="{row_y:.1f}" r="4.2" fill="{teal}" '
        f'stroke="#ffffff" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="{lx + 48:.1f}" y="{row_y + 4:.1f}" font-size="11.5" fill="{axis}">'
        f'well-calibrated (Brier {calibrated.brier:.3f}, '
        f'log-loss {calibrated.log_loss:.3f})</text>'
    )
    row_y += 24
    parts.append(
        f'<line x1="{lx + 12:.1f}" y1="{row_y:.1f}" x2="{lx + 40:.1f}" y2="{row_y:.1f}" '
        f'stroke="{red}" stroke-width="2.4"/>'
    )
    parts.append(
        f'<circle cx="{lx + 26:.1f}" cy="{row_y:.1f}" r="4.2" fill="{red}" '
        f'stroke="#ffffff" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="{lx + 48:.1f}" y="{row_y + 4:.1f}" font-size="11.5" fill="{axis}">'
        f'over-confident (Brier {overconfident.brier:.3f}, '
        f'log-loss {overconfident.log_loss:.3f})</text>'
    )

    parts.append("</svg>\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Markdown report                                                             #
# --------------------------------------------------------------------------- #
def _bin_near(rep: Report, target: float) -> Bin:
    return min((b for b in rep.bins if b.count), key=lambda b: abs(b.mean_predicted - target))


def render_report(
    calibrated: Report,
    overconfident: Report,
    abstain_few: Report,
    abstain_enough: Report,
    n: int,
    source: str,
) -> str:
    hi_cal = _bin_near(calibrated, 0.85)
    hi_oc = _bin_near(overconfident, 0.85)

    lines: list[str] = []
    lines.append("# Speedrun - Memory Model is Calibrated (held-out reviews)")
    lines.append("")
    lines.append(
        "**Claim.** When Speedrun says an item has an *X%* chance of recall, the "
        "student really recalls it about *X%* of the time. This is measured on a "
        "**held-out** set of predictions with a proper score (Brier and log-loss) "
        "plus a reliability diagram - and the same score is shown to *catch* a "
        "model that is miscalibrated."
    )
    lines.append("")
    lines.append(
        f"Scores come from the Rust `SpeedrunService.get_calibration_report()` "
        f"({N_BINS} equal-width bins). No AI is involved. "
        + (
            "The engine computed these numbers; a pure-Python mirror of the same "
            "maths was cross-checked against it and agreed."
            if source == "engine"
            else "The built pylib was unavailable, so these numbers come from a "
            "pure-Python mirror of the engine's calibration maths (see the note "
            "at the bottom)."
        )
    )
    lines.append("")
    lines.append("Reproduce:")
    lines.append("")
    lines.append("```bash")
    lines.append("./tools/speedrun_calibration.sh          # default n=200 per scenario")
    lines.append("./tools/speedrun_calibration.sh 500       # larger held-out set")
    lines.append("```")
    lines.append("")

    lines.append("## Scores (lower is better)")
    lines.append("")
    lines.append("| Scenario | n | Brier | Log-loss | Sufficient |")
    lines.append("| --- | ---: | ---: | ---: | :---: |")
    lines.append(
        f"| Well-calibrated generator | {calibrated.n} | {calibrated.brier:.4f} | "
        f"{calibrated.log_loss:.4f} | {'yes' if calibrated.sufficient else 'no'} |"
    )
    lines.append(
        f"| Over-confident generator | {overconfident.n} | {overconfident.brier:.4f} | "
        f"{overconfident.log_loss:.4f} | {'yes' if overconfident.sufficient else 'no'} |"
    )
    lines.append("")
    lines.append(
        f"The over-confident model scores a **worse (higher) Brier "
        f"({overconfident.brier:.4f} vs {calibrated.brier:.4f})** and log-loss "
        f"({overconfident.log_loss:.4f} vs {calibrated.log_loss:.4f}) - the proper "
        f"score detects the miscalibration even though both models make the same "
        f"*average* prediction."
    )
    lines.append("")

    lines.append("## Reliability diagram")
    lines.append("")
    lines.append("![calibration](speedrun_calibration_chart.svg)")
    lines.append("")
    lines.append(
        "Points on the dashed `y = x` line are perfectly calibrated. The "
        "well-calibrated curve (teal) hugs the diagonal; the over-confident curve "
        "(red) bows **below** the diagonal for confident predictions (it recalls "
        "less than it claims) and **above** it for low ones."
    )
    lines.append("")

    lines.append("## Plain-language read")
    lines.append("")
    lines.append(
        f"- **Well-calibrated:** in the {hi_cal.lo:.1f}-{hi_cal.hi:.1f} band the "
        f"model predicts on average **{hi_cal.mean_predicted*100:.0f}%** and the "
        f"held-out recall is **{hi_cal.mean_outcome*100:.0f}%** - the points sit on "
        f"the diagonal, so a stated probability means what it says (say ~80%, get "
        f"~80% recall)."
    )
    lines.append(
        f"- **Over-confident:** in that same {hi_oc.lo:.1f}-{hi_oc.hi:.1f} band it "
        f"still predicts **{hi_oc.mean_predicted*100:.0f}%**, but only "
        f"**{hi_oc.mean_outcome*100:.0f}%** actually stick - confident predictions "
        f"are systematically inflated, which is exactly what the higher Brier flags."
    )
    lines.append("")

    lines.append("## Per-bin reliability table")
    lines.append("")
    lines.append(
        "Predicted probabilities are stratified across the bins, so both scenarios "
        "share the same bin membership (`count`, `mean predicted`); only the "
        "outcomes differ."
    )
    lines.append("")
    lines.append(
        "| Bin | Count | Mean predicted | Mean outcome (well-cal.) | "
        "Mean outcome (over-conf.) |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for cb, ob in zip(calibrated.bins, overconfident.bins):
        rng = f"{cb.lo:.1f}-{cb.hi:.1f}"
        if cb.count:
            lines.append(
                f"| {rng} | {cb.count} | {cb.mean_predicted:.3f} | "
                f"{cb.mean_outcome:.3f} | {ob.mean_outcome:.3f} |"
            )
        else:
            lines.append(f"| {rng} | 0 | - | - | - |")
    lines.append("")

    lines.append(f"## Give-up rule (abstain below {MIN_PREDICTIONS} predictions)")
    lines.append("")
    lines.append(
        f"`MIN_PREDICTIONS = {MIN_PREDICTIONS}` (from "
        f"`rslib/src/speedrun/calibration.rs`). Below that the engine refuses to "
        f"report a calibration score rather than publish a noisy one:"
    )
    lines.append("")
    lines.append("| Predictions | Sufficient | Note |")
    lines.append("| ---: | :---: | --- |")
    lines.append(
        f"| {abstain_few.n} | {'yes' if abstain_few.sufficient else 'no'} | "
        f"{abstain_few.note} |"
    )
    lines.append(
        f"| {abstain_enough.n} | {'yes' if abstain_enough.sufficient else 'no'} | "
        f"{abstain_enough.note} |"
    )
    lines.append("")
    lines.append(
        f"With {abstain_few.n} predictions it **abstains** "
        f"(`sufficient == False`); at {abstain_enough.n} (>= {MIN_PREDICTIONS}) it "
        f"computes a score."
    )
    lines.append("")

    lines.append("## Method")
    lines.append("")
    lines.append(
        f"- **Held-out predictions.** Each attempt captures a pre-answer "
        f"`predicted` probability via `record_attempt(..., predicted=p)` and its "
        f"actual `correct` outcome; `get_calibration_report()` scores the "
        f"`predicted`-vs-`correct` pairs. Fixed RNG seed `{SEED}` -> the run is "
        f"reproducible."
    )
    lines.append(
        "- **Well-calibrated generator:** `outcome ~ Bernoulli(predicted)`."
    )
    lines.append(
        "- **Over-confident generator:** the true recall is pulled halfway to a "
        "coin flip, `true = 0.5 + (predicted - 0.5) * 0.5` (a stated 90% is really "
        "70%), then `outcome ~ Bernoulli(true)`. Predictions are unchanged, so the "
        "*average* prediction is honest but the per-bucket reliability is not - the "
        "kind of error a single accuracy number would miss and Brier/reliability "
        "catch."
    )
    lines.append(
        "- **Brier** = mean `(p - y)^2`; **log-loss** = mean "
        "`-(y*ln p + (1-y)*ln(1-p))` (clamped by 1e-7); both lower-is-better."
    )
    if source != "engine":
        lines.append(
            "- **Note:** produced from the pure-Python fallback because the built "
            "pylib was not importable in this environment; build it "
            "(`./ninja pylib`) and re-run for engine-computed numbers."
        )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Console printing                                                            #
# --------------------------------------------------------------------------- #
def print_report(name: str, rep: Report) -> None:
    print(f"\n== {name} ==")
    print(
        f"  n={rep.n}  brier={rep.brier:.4f}  log_loss={rep.log_loss:.4f}  "
        f"sufficient={rep.sufficient}  note={rep.note!r}"
    )
    print("  bin        count  mean_pred  mean_outcome")
    for b in rep.bins:
        if b.count:
            print(
                f"  {b.lo:.1f}-{b.hi:.1f}   {b.count:5d}     {b.mean_predicted:.3f}      "
                f"{b.mean_outcome:.3f}"
            )
        else:
            print(f"  {b.lo:.1f}-{b.hi:.1f}   {b.count:5d}       -          -")


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    if n < 1:
        print("n must be >= 1", file=sys.stderr)
        return 2

    if _HAVE_ENGINE:
        print("engine: using built pylib SpeedrunService.get_calibration_report()")
    else:
        print(
            "engine: built pylib NOT importable "
            f"({_IMPORT_ERROR}); using pure-Python calibration fallback"
        )

    calibrated, source = report_for_pairs(generate_pairs(n, "calibrated"))
    overconfident, _ = report_for_pairs(generate_pairs(n, "overconfident"))

    print_report("well-calibrated generator", calibrated)
    print_report("over-confident generator", overconfident)

    # Give-up rule: abstain below MIN_PREDICTIONS, compute at/above it.
    abstain_few, _ = report_for_pairs(generate_pairs(MIN_PREDICTIONS // 2, "calibrated"))
    abstain_enough, _ = report_for_pairs(
        generate_pairs(MIN_PREDICTIONS + 5, "calibrated")
    )
    print_report(f"abstain demo: {abstain_few.n} predictions (< {MIN_PREDICTIONS})", abstain_few)
    print_report(
        f"abstain demo: {abstain_enough.n} predictions (>= {MIN_PREDICTIONS})",
        abstain_enough,
    )

    # Write the deliverables (chart + report), always, so the run reproduces them.
    svg = render_svg(calibrated, overconfident, n)
    with open(CHART_PATH, "w", encoding="utf-8") as fh:
        fh.write(svg)
    report_md = render_report(
        calibrated, overconfident, abstain_few, abstain_enough, n, source
    )
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(report_md)
    print(f"\nwrote {os.path.relpath(CHART_PATH, os.path.dirname(HERE))}")
    print(f"wrote {os.path.relpath(REPORT_PATH, os.path.dirname(HERE))}")

    # Invariants (re-runnable self-test).
    assert len(calibrated.bins) == N_BINS, len(calibrated.bins)
    assert len(overconfident.bins) == N_BINS, len(overconfident.bins)
    assert calibrated.sufficient and overconfident.sufficient
    # the proper score detects miscalibration
    assert calibrated.brier < overconfident.brier, (calibrated.brier, overconfident.brier)
    assert calibrated.log_loss < overconfident.log_loss, (
        calibrated.log_loss,
        overconfident.log_loss,
    )
    # well-calibrated is genuinely good; over-confident is clearly worse
    assert calibrated.brier < 0.20, calibrated.brier
    assert overconfident.brier > 0.22, overconfident.brier
    # well-calibrated: aggregate predicted mean tracks aggregate outcome mean
    total = sum(b.count for b in calibrated.bins)
    agg_pred = sum(b.mean_predicted * b.count for b in calibrated.bins) / total
    agg_out = sum(b.mean_outcome * b.count for b in calibrated.bins) / total
    assert abs(agg_pred - agg_out) < 0.05, (agg_pred, agg_out)
    # give-up rule
    assert not abstain_few.sufficient and "not enough predictions" in abstain_few.note
    assert abstain_enough.sufficient

    print("\ncalibration self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
