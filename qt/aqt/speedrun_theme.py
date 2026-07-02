# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun visual layer (presentation only).

Holds the Apple-style / Anki-3.0-direction CSS and the HTML builders for the
in-place Speedrun surfaces:

- a compact readiness banner embedded on the deck-list home,
- a full readiness panel embedded on the per-deck overview (three signals, the
  recall->performance "bridge", coverage ring, exam plan, calibration, and a
  routed next-action), with a first-class honest-abstention state.

This module performs no backend calls: it renders a plain ``data`` dict produced
by ``speedrun.py`` into HTML. Colours adapt to Anki light/dark via CSS custom
properties keyed on the ``night-mode`` class / ``data-bs-theme`` attribute.
"""

from __future__ import annotations

from html import escape

# --- design tokens ----------------------------------------------------------

# Apple "clinical daylight" tokens; dark values mirror Anki night mode. These
# are the single source of truth for the Speedrun look.
_TOKENS = """
:root {
  --sr-canvas:#F2F3F5; --sr-surface:#FFFFFF; --sr-ink:#16181D; --sr-secondary:#6B7280;
  --sr-hairline:#E7EAEE; --sr-memory:#2E7BF6; --sr-perf:#22C55E; --sr-accent:#2E7BF6;
  --sr-amber:#E0900B; --sr-radius:20px;
  --sr-shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06);
  --sr-font:-apple-system,"SF Pro Display","SF Pro Text","Inter",system-ui,"Segoe UI",Roboto,sans-serif;
}
.night-mode, [data-bs-theme="dark"] {
  --sr-canvas:#0C0D0F; --sr-surface:#17181B; --sr-ink:#F2F3F5; --sr-secondary:#9AA0A8;
  --sr-memory:#4B93FF; --sr-perf:#30D158; --sr-accent:#4B93FF; --sr-amber:#FBBF24;
  --sr-hairline:rgba(255,255,255,.10);
  --sr-shadow:0 1px 2px rgba(0,0,0,.3), 0 10px 30px rgba(0,0,0,.45);
}
"""

# Styling for the Speedrun components themselves. Always injected wherever a
# component is rendered, so the embeds look right even with the reskin off.
_COMPONENTS = """
.sr-panel, .sr-banner { font-family: var(--sr-font); color: var(--sr-ink);
  -webkit-font-smoothing: antialiased; text-align: left; }
.sr-panel * , .sr-banner * { box-sizing: border-box; }
.sr-panel { max-width: 760px; margin: 22px auto; display: flex; flex-direction: column; gap: 14px; }

.sr-card { background: var(--sr-surface); border: 1px solid var(--sr-hairline);
  border-radius: var(--sr-radius); box-shadow: var(--sr-shadow); padding: 18px 20px; }
.sr-eyebrow { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: var(--sr-secondary); margin: 0 0 6px; }

/* hero readiness */
.sr-hero { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.sr-hero .sr-score { font-size: 52px; font-weight: 600; line-height: 1;
  font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
.sr-hero .sr-range { font-size: 15px; color: var(--sr-secondary); font-variant-numeric: tabular-nums; }
.sr-hero .sr-scale { font-size: 12px; color: var(--sr-secondary); }
.sr-updated { margin: 10px 0 0; font-size: 12px; color: var(--sr-secondary); }

/* honest abstention */
.sr-abstain .sr-score { font-size: 24px; font-weight: 600; color: var(--sr-amber); }
.sr-abstain p { margin: 8px 0 0; font-size: 13px; color: var(--sr-secondary); line-height: 1.5; }
.sr-block { color: var(--sr-ink); font-weight: 600; }

/* signature readiness ring (mirrors the phone) */
.sr-herocard { display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; }
.sr-herocard .sr-eyebrow { align-self: flex-start; }
.sr-hero-ring { width: 158px; height: 158px; border-radius: 50%; margin: 8px 0 4px; flex: none;
  background: conic-gradient(var(--sr-memory), var(--sr-perf) var(--frac, 0deg), var(--sr-hairline) 0);
  display: grid; place-items: center; }
.sr-hero-ring.sr-ring-empty { background: var(--sr-hairline); }
.sr-hole { width: 126px; height: 126px; border-radius: 50%; background: var(--sr-surface);
  display: flex; flex-direction: column; align-items: center; justify-content: center; }
.sr-hole .sr-num { font-size: 46px; font-weight: 600; line-height: 1; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; color: var(--sr-ink); }
.sr-hole .sr-num.sr-muted { font-size: 34px; color: var(--sr-amber); }
.sr-hole .sr-holelbl { font-size: 11px; color: var(--sr-secondary); margin-top: 5px;
  text-transform: uppercase; letter-spacing: .06em; }
.sr-herocard .sr-range { font-size: 14px; color: var(--sr-secondary); font-variant-numeric: tabular-nums; }
.sr-herocard p { margin: 4px 0 0; font-size: 13px; color: var(--sr-secondary); line-height: 1.5; }

/* three signals */
.sr-signals { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
@media (max-width: 560px) { .sr-signals { grid-template-columns: 1fr; } }
.sr-signal .sr-val { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-signal .sr-name { font-size: 12px; color: var(--sr-secondary); margin-top: 2px; }
.sr-bar { height: 6px; border-radius: 3px; background: var(--sr-hairline); margin-top: 10px; overflow: hidden; }
.sr-bar > i { display: block; height: 100%; border-radius: 3px; }
.sr-thin { font-size: 11px; color: var(--sr-amber); margin-top: 6px; }

/* recall -> performance bridge (signature) */
.sr-bridge { display: flex; align-items: center; gap: 14px; }
.sr-node { text-align: center; min-width: 84px; }
.sr-node b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; }
.sr-node span { font-size: 11px; color: var(--sr-secondary); }
.sr-span { flex: 1; position: relative; height: 2px; background: var(--sr-hairline); }
.sr-span em { position: absolute; top: -11px; left: 50%; transform: translateX(-50%);
  background: var(--sr-surface); padding: 0 8px; font-style: normal; font-size: 12px; font-weight: 600;
  color: var(--sr-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }

/* small stat grid: coverage ring, exam, calibration */
.sr-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
@media (max-width: 560px) { .sr-grid { grid-template-columns: 1fr; } }
.sr-mini { display: flex; align-items: center; gap: 12px; }
.sr-ring { width: 56px; height: 56px; border-radius: 50%; flex: none;
  background: conic-gradient(var(--sr-accent) var(--sr-ringval, 0deg), var(--sr-hairline) 0);
  display: grid; place-items: center; }
.sr-ring > span { width: 42px; height: 42px; border-radius: 50%; background: var(--sr-surface);
  display: grid; place-items: center; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-k { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-s { font-size: 12px; color: var(--sr-secondary); }

/* next action */
.sr-next { display: flex; align-items: center; gap: 14px; border-left: 3px solid var(--sr-accent); }
.sr-next .sr-t { font-weight: 600; font-size: 15px; }
.sr-next .sr-d { font-size: 13px; color: var(--sr-secondary); margin-top: 3px; line-height: 1.5; }

/* actions */
.sr-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sr-btn { font: 500 13px var(--sr-font); color: var(--sr-ink); cursor: pointer;
  background: var(--sr-surface); border: 1px solid var(--sr-hairline); border-radius: 980px;
  padding: 7px 14px; transition: background .15s, border-color .15s; }
.sr-btn:hover { border-color: var(--sr-accent); }
.sr-btn.sr-primary { background: var(--sr-ink); border-color: var(--sr-ink); color: var(--sr-surface); font-weight: 600; }
.sr-btn.sr-primary:hover { opacity: .9; border-color: var(--sr-ink); }
.sr-toggle { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--sr-secondary);
  cursor: pointer; border: 1px solid var(--sr-hairline); border-radius: 980px; padding: 6px 12px; }
.sr-toggle[data-on="1"] { color: var(--sr-ink); border-color: var(--sr-accent); }
.sr-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--sr-hairline); }
.sr-toggle[data-on="1"] .sr-dot { background: var(--sr-perf); }

/* deck-home banner */
.sr-banner { max-width: 760px; margin: 18px auto; background: var(--sr-surface);
  border: 1px solid var(--sr-hairline); border-radius: var(--sr-radius); box-shadow: var(--sr-shadow);
  padding: 14px 18px; display: flex; align-items: center; gap: 16px; }
.sr-banner .sr-lead { font-size: 30px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.sr-banner .sr-lead.sr-muted { font-size: 17px; color: var(--sr-amber); }
.sr-banner .sr-meta { flex: 1; }
.sr-banner .sr-meta b { font-size: 14px; } .sr-banner .sr-meta div { font-size: 12px; color: var(--sr-secondary); margin-top: 2px; }
.sr-chip { font-size: 11px; color: var(--sr-secondary); border: 1px solid var(--sr-hairline);
  border-radius: 980px; padding: 3px 9px; white-space: nowrap; }
"""

# Moderate reskin of Anki's own chrome on the deck browser / overview. Kept to
# safe properties (colour, font, radius, spacing) so it can't break layouts; the
# reviewer card is deliberately never touched. Injected only when the opt-in
# `speedrunModernUi` toggle is on.
_RESKIN = """
body { background: var(--sr-canvas) !important; color: var(--sr-ink);
  font-family: var(--sr-font); -webkit-font-smoothing: antialiased; }
a { color: var(--sr-accent); }
button, .btn { font-family: var(--sr-font) !important; border-radius: 10px !important; cursor: pointer; }
table.deck td, tr.deck td { padding-top: 7px !important; padding-bottom: 7px !important; }
"""

_TOOLBAR_RESKIN = """
#header, .toolbar { background: var(--sr-canvas) !important;
  border-bottom: 1px solid var(--sr-hairline) !important; }
.hitem { font-family: var(--sr-font); }
#speedrun { color: var(--sr-accent); font-weight: 600; }
"""


def component_style() -> str:
    """Tokens + component CSS, wrapped in a <style> tag (always safe to inject)."""
    return f"<style>{_TOKENS}{_COMPONENTS}</style>"


# Color-coded, rounded answer buttons for the reviewer bottom bar. Targets the
# native answer buttons by their data-ease attribute (chrome only, not the card),
# with the interval time stacked beneath each label.
_ANSWER_BUTTONS = """
button[data-ease] {
  font-family: var(--sr-font) !important;
  border-radius: 12px !important;
  border: 1px solid var(--sr-hairline) !important;
  background: var(--sr-surface) !important;
  color: var(--sr-ink) !important;
  padding: 8px 16px !important; margin: 0 4px !important; cursor: pointer;
}
button[data-ease]:hover { box-shadow: var(--sr-shadow); }
button[data-ease] .nobold { display: block; margin-top: 2px; font-size: 11px;
  font-weight: 400; color: var(--sr-secondary); }
button[data-ease="1"] { border-color: rgba(255,59,48,.55) !important; color: #FF3B30 !important; }
button[data-ease="2"] { border-color: rgba(255,159,10,.55) !important; color: #FF9F0A !important; }
button[data-ease="3"] { border-color: rgba(52,199,89,.55) !important; color: #34C759 !important; }
button[data-ease="4"] { border-color: rgba(10,132,255,.55) !important; color: #0A84FF !important; }
#ansbut { font-family: var(--sr-font) !important; border-radius: 12px !important;
  border: none !important; background: var(--sr-accent) !important; color: #fff !important;
  padding: 8px 22px !important; cursor: pointer; }
"""


def reskin_style(kind: str) -> str:
    """Opt-in native-screen reskin CSS for 'screen' or 'toolbar'."""
    body = _TOOLBAR_RESKIN if kind == "toolbar" else _RESKIN
    return f"<style>{_TOKENS}{body}</style>"


def answer_buttons_css() -> str:
    """Color-coded Apple-style answer buttons for the reviewer bottom bar."""
    return f"<style>{_TOKENS}{_ANSWER_BUTTONS}</style>"


# --- helpers ----------------------------------------------------------------


def _pct(x: float) -> int:
    return int(round(max(0.0, min(1.0, x)) * 100))


def _bar(frac: float, color: str) -> str:
    return (
        f'<div class="sr-bar"><i style="width:{_pct(frac)}%;background:{color}"></i></div>'
    )


def _signal(name: str, frac: float, color: str, thin: bool) -> str:
    warn = '<div class="sr-thin">thin evidence</div>' if thin else ""
    return (
        f'<div class="sr-card sr-signal"><div class="sr-val">{_pct(frac)}%</div>'
        f'<div class="sr-name">{escape(name)}</div>{_bar(frac, color)}{warn}</div>'
    )


def _bridge(memory: float, performance: float, gap: float) -> str:
    return (
        '<div class="sr-card"><p class="sr-eyebrow">Recall &rarr; performance bridge</p>'
        '<div class="sr-bridge">'
        f'<div class="sr-node"><b>{_pct(memory)}%</b><span>Memory</span></div>'
        f'<div class="sr-span"><em>{gap * 100:+.0f} pts</em></div>'
        f'<div class="sr-node"><b>{_pct(performance)}%</b><span>Performance</span></div>'
        "</div></div>"
    )


# --- banner (deck home) -----------------------------------------------------


def banner_html(data: dict) -> str:
    if data.get("sufficient"):
        lead = f'<div class="sr-lead">{data["readiness"]}</div>'
        meta = (
            f'<div class="sr-meta"><b>Projected MCAT readiness</b>'
            f'<div>Likely {data["low"]}&ndash;{data["high"]} &middot; '
            f'memory {_pct(data["memory"])}% &middot; performance {_pct(data["performance"])}%</div></div>'
        )
    else:
        lead = '<div class="sr-lead sr-muted">No score yet</div>'
        meta = (
            f'<div class="sr-meta"><b>Readiness withheld &mdash; not enough evidence</b>'
            f'<div>{escape(data.get("reason", ""))}</div></div>'
        )
    cov = f'<span class="sr-chip">{_pct(data["coverage"])}% covered</span>'
    return f'{component_style()}<div class="sr-banner">{lead}{meta}{cov}</div>'


# --- panel (per-deck overview) ---------------------------------------------


def _hero(data: dict) -> str:
    if data.get("sufficient"):
        frac = max(0.0, min(1.0, (data["readiness"] - 472) / 56.0))
        deg = round(frac * 360)
        return (
            '<div class="sr-card sr-herocard">'
            '<p class="sr-eyebrow">Readiness &middot; MCAT 472&ndash;528</p>'
            f'<div class="sr-hero-ring" style="--frac:{deg}deg"><div class="sr-hole">'
            f'<span class="sr-num">{data["readiness"]}</span>'
            '<span class="sr-holelbl">projected</span></div></div>'
            f'<p class="sr-range">Likely {data["low"]}&ndash;{data["high"]}</p>'
            f'<p>Updated {escape(data.get("updated", "just now"))}.</p></div>'
        )
    block = data.get("blocking", "")
    block_line = (
        f'<p>Weakest dimension right now: <span class="sr-block">{escape(block)}</span>.</p>'
        if block and block != "none"
        else ""
    )
    return (
        '<div class="sr-card sr-herocard sr-abstain">'
        '<p class="sr-eyebrow">Readiness &middot; MCAT 472&ndash;528</p>'
        '<div class="sr-hero-ring sr-ring-empty"><div class="sr-hole">'
        '<span class="sr-num sr-muted">&mdash;</span>'
        '<span class="sr-holelbl">no score yet</span></div></div>'
        f'<p>{escape(data.get("reason", ""))}</p>{block_line}</div>'
    )


def _signals(data: dict) -> str:
    return (
        '<div class="sr-signals">'
        + _signal("Memory", data["memory"], "var(--sr-memory)", not data.get("memory_ok", True))
        + _signal("Performance", data["performance"], "var(--sr-perf)", not data.get("perf_ok", True))
        + _signal("Coverage", data["coverage"], "var(--sr-secondary)", False)
        + "</div>"
    )


def _mini_grid(data: dict) -> str:
    ring_deg = _pct(data["coverage"]) * 3.6
    coverage = (
        '<div class="sr-card sr-mini">'
        f'<div class="sr-ring" style="--sr-ringval:{ring_deg}deg"><span>{_pct(data["coverage"])}%</span></div>'
        f'<div><div class="sr-k">{data["cov_covered"]}/{data["cov_total"]}</div>'
        '<div class="sr-s">topics covered</div></div></div>'
    )

    exam = data.get("exam")
    if exam and exam.get("has"):
        if exam.get("readiness_sufficient"):
            status = "on track" if exam.get("on_track") else f'need +{exam.get("needed", 0)}'
        else:
            status = "gathering evidence"
        exam_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">'
            f'{exam.get("days_left", 0)}d</div>'
            f'<div class="sr-s">to exam &middot; {escape(exam.get("mode", ""))}</div>'
            f'<div class="sr-s">{escape(status)}</div></div></div>'
        )
    else:
        exam_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">&mdash;</div>'
            '<div class="sr-s">no exam target</div></div></div>'
        )

    cal = data.get("calibration")
    if cal and cal.get("sufficient"):
        cal_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">'
            f'{cal.get("brier", 0):.2f}</div>'
            f'<div class="sr-s">Brier &middot; n={cal.get("n", 0)}</div></div></div>'
        )
    else:
        cal_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">&middot;&middot;&middot;</div>'
            '<div class="sr-s">calibration building</div></div></div>'
        )
    return f'<div class="sr-grid">{coverage}{exam_html}{cal_html}</div>'


def _next_action(data: dict) -> str:
    na = data.get("next_action") or {}
    title = na.get("title", "Keep studying")
    detail = na.get("detail", "")
    cmd = na.get("cmd")
    cta = na.get("cta")
    button = (
        f'<button class="sr-btn sr-primary" onclick="pycmd(\'{escape(cmd)}\')">{escape(cta)}</button>'
        if cmd and cta
        else ""
    )
    return (
        '<div class="sr-card sr-next"><div style="flex:1">'
        '<p class="sr-eyebrow">Next best action</p>'
        f'<div class="sr-t">{escape(title)}</div>'
        f'<div class="sr-d">{escape(detail)}</div></div>'
        f"{button}</div>"
    )


def _actions(data: dict) -> str:
    def toggle(cmd: str, label: str, on: bool, tip: str = "") -> str:
        tip_attr = f' title="{escape(tip)}"' if tip else ""
        return (
            f'<span class="sr-toggle" data-on="{1 if on else 0}"{tip_attr} '
            f"onclick=\"pycmd('{cmd}')\"><span class=\"sr-dot\"></span>{escape(label)}</span>"
        )

    seed = ""
    if data.get("cov_total", 0) == 0:
        seed = "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:seed')\">Seed MCAT topics</button>"
    exam_label = "Edit exam target" if (data.get("exam") or {}).get("has") else "Set exam target"
    return (
        '<div class="sr-actions">'
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:practice')\">Practice questions</button>"
        f"{seed}"
        f"<button class=\"sr-btn\" onclick=\"pycmd('speedrun:exam')\">{exam_label}</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:refresh')\">Refresh</button>"
        + toggle(
            "speedrun:toggle:points",
            "Points-at-stake",
            data.get("points_at_stake", False),
            "Order due cards by weakness (points at stake).",
        )
        + toggle(
            "speedrun:toggle:interleave",
            "Spaced + interleaved practice",
            data.get("interleave", False),
            "Distributed practice is FSRS; this interleaves confusable sibling "
            "topics (same parent tag) in reviews and new cards, keeping unrelated "
            "concepts blocked.",
        )
        + toggle(
            "speedrun:toggle:modern",
            "Modern UI",
            data.get("modern_ui", True),
            "Apple-style reskin of Anki's deck list, overview, and toolbar.",
        )
        + "</div>"
    )


def panel_html(data: dict) -> str:
    """Full Speedrun panel for the per-deck overview."""
    return (
        component_style()
        + '<div class="sr-panel">'
        + _hero(data)
        + _signals(data)
        + _bridge(data["memory"], data["performance"], data.get("gap", 0.0))
        + _mini_grid(data)
        + _next_action(data)
        + _actions(data)
        + "</div>"
    )
