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

import json
import math
from html import escape

# --- design tokens (single source of truth) ---------------------------------

# ONE Python structure holds the whole Speedrun palette (light + dark), matching
# ``speedrun_design_spec.md`` exactly. From it we emit (a) the web ``:root`` /
# ``.night-mode`` CSS custom properties, (b) the Qt QSS palette used by the
# dialogs + global chrome, and (c) the reviewer answer-button colours -- so there
# is no longer any hand-copied second/third palette that can drift.
#
# Font + display stacks are shared by web (@font-face + var) and Qt.
_FONT_STACK = (
    '"Geist",-apple-system,"SF Pro Text","Inter",system-ui,"Segoe UI",Roboto,sans-serif'
)
_DISPLAY_STACK = '"Fraunces","Geist",Georgia,"Times New Roman",serif'

# Bundled OFL fonts, served by Anki's media server from aqt/data/web/imgs/ at
# /_anki/imgs/. Variable weight axes let one file cover the whole range. These
# mirror the mobile identity: Geist (product sans) + Fraunces (display serif).
_FONTS = """
@font-face { font-family:"Geist"; font-style:normal; font-weight:100 900;
  font-display:swap; src:url("/_anki/imgs/speedrun-geist.ttf") format("truetype"); }
@font-face { font-family:"Fraunces"; font-style:normal; font-weight:100 900;
  font-display:swap; src:url("/_anki/imgs/speedrun-fraunces.ttf") format("truetype"); }
"""

# token -> (light, dark). ``hairline_web`` keeps the spec's translucent dark
# separator; ``hairline`` is the opaque value Qt needs (QSS ignores rgba borders
# inconsistently). ``on_signal`` is dark ink for text/icons on amber/green fills
# (white is used directly on blue/red).
# Palette: a warm "paper & print" identity. Light = Pampas cream paper (#FAF9F5)
# with charcoal ink and the signature Crail peach accent; Dark = charcoal paper
# (#141413) with Pampas text and a muted accent blue (the print/dark pairing).
# The data-signal hues (memory/perf/coverage/reasoning/passage) stay distinct
# functional colors so the brand accent never doubles as a chart encoding and the
# readiness gauge's memory/performance arcs read apart.
_LIGHT: dict[str, str] = {
    "canvas": "#FAF9F5",
    "surface": "#FFFFFF",
    "elevated": "#FFFFFF",
    "ink": "#141413",
    "secondary": "#6B6862",
    "tertiary": "#A6A299",
    "hairline_web": "#E8E6DC",
    "hairline": "#E8E6DC",
    "accent": "#CC785C",
    "memory": "#2E7BF6",
    "perf": "#22C55E",
    "coverage": "#8A94A6",
    "reasoning": "#7C5CFC",
    "passage": "#0E9AA7",
    "amber": "#E0900B",
    "danger": "#EF4444",
    "on_signal": "#141413",
    "field": "#FFFFFF",
    "shadow_sm": "0 1px 2px rgba(60,50,30,.05)",
    "shadow": "0 1px 2px rgba(60,50,30,.04), 0 8px 24px rgba(60,50,30,.07)",
    "shadow_lg": "0 12px 32px rgba(60,50,30,.12)",
}
_DARK: dict[str, str] = {
    "canvas": "#100F0D",
    "surface": "#1C1B18",
    "elevated": "#232220",
    "ink": "#FAF9F5",
    "secondary": "#B0AEA5",
    "tertiary": "#7C7970",
    "hairline_web": "rgba(255,255,255,.09)",
    "hairline": "#33312D",
    "accent": "#D98A6B",
    "memory": "#4B93FF",
    "perf": "#788C5D",
    "coverage": "#6B7280",
    "reasoning": "#A78BFA",
    "passage": "#2DD4BF",
    "amber": "#FBBF24",
    "danger": "#FF6B6B",
    "on_signal": "#141413",
    "field": "#232220",
    "shadow_sm": "0 1px 2px rgba(0,0,0,.3)",
    "shadow": "0 1px 2px rgba(0,0,0,.3), 0 10px 30px rgba(0,0,0,.45)",
    "shadow_lg": "0 14px 40px rgba(0,0,0,.5)",
}

# Unified radii (spec): card 20, control/input 12, primary CTA + chips pill.
_RADII = {"card": "20px", "input": "12px", "pill": "999px"}


def _css_vars(t: dict[str, str]) -> str:
    """Render one mode's custom properties (web uses the translucent hairline)."""
    return (
        f"--sr-canvas:{t['canvas']}; --sr-surface:{t['surface']}; "
        f"--sr-elevated:{t['elevated']}; --sr-ink:{t['ink']}; "
        f"--sr-secondary:{t['secondary']}; --sr-tertiary:{t['tertiary']}; "
        f"--sr-hairline:{t['hairline_web']}; --sr-accent:{t['accent']}; "
        f"--sr-memory:{t['memory']}; --sr-perf:{t['perf']}; "
        f"--sr-coverage:{t['coverage']}; --sr-reasoning:{t['reasoning']}; "
        f"--sr-passage:{t['passage']}; --sr-amber:{t['amber']}; "
        f"--sr-danger:{t['danger']}; --sr-on-signal:{t['on_signal']}; "
        f"--sr-shadow-sm:{t['shadow_sm']}; --sr-shadow:{t['shadow']}; "
        f"--sr-shadow-lg:{t['shadow_lg']}; "
        f"--sr-radius:{_RADII['card']}; --sr-radius-input:{_RADII['input']}; "
        f"--sr-radius-pill:{_RADII['pill']};"
    )


_TOKENS = (
    _FONTS
    + f"""
:root {{
  {_css_vars(_LIGHT)}
  --sr-font:{_FONT_STACK};
  --sr-display:{_DISPLAY_STACK};
}}
.night-mode, [data-bs-theme="dark"] {{
  {_css_vars(_DARK)}
}}
"""
)

# Styling for the Speedrun components themselves. Always injected wherever a
# component is rendered, so the embeds look right even with the reskin off.
_COMPONENTS = """
.sr-panel, .sr-banner { font-family: var(--sr-font); color: var(--sr-ink);
  -webkit-font-smoothing: antialiased; text-align: left; }
.sr-panel * , .sr-banner * { box-sizing: border-box; }
.sr-panel { max-width: 760px; margin: 22px auto; display: flex; flex-direction: column; gap: 14px; }

.sr-card { background: var(--sr-elevated); border: 1px solid var(--sr-hairline);
  border-radius: var(--sr-radius); box-shadow: var(--sr-shadow); padding: 18px 20px; }
.sr-eyebrow { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: var(--sr-secondary); margin: 0 0 6px; }

/* honest abstention text: one clean caption + a "weakest" chip (no stacked,
   competing lines that read as two captions under the gauge) */
.sr-abstain-reason { margin: 14px auto 0; max-width: 40ch; font-size: 13px;
  color: var(--sr-secondary); line-height: 1.55; text-align: center; }
.sr-block-chip { margin-top: 12px; display: inline-flex; align-items: center;
  padding: 4px 12px; border-radius: var(--sr-radius-pill); font-size: 12px; font-weight: 600;
  color: var(--sr-ink); background: color-mix(in srgb, var(--sr-amber) 16%, transparent);
  text-transform: capitalize; }
.sr-block { color: var(--sr-ink); font-weight: 600; }

/* Fraunces display serif on the signature numbers (mirrors the phone) */
.sr-readout, .sr-banner .sr-lead { font-family: var(--sr-display); font-weight: 600; }

/* signature readiness instrument: two distinct arcs (memory blue + performance
   green), a thin outer coverage track, the low-high range as a band + marker,
   and the composite score as a Fraunces readout in the elevated hole. */
.sr-herocard { display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; }
.sr-herocard .sr-eyebrow { align-self: flex-start; }
.sr-gauge { position: relative; width: 176px; height: 176px; margin: 6px 0 2px; flex: none; }
.sr-gauge svg { width: 176px; height: 176px; display: block; }
.sr-gauge-center { position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; text-align: center; }
.sr-readout { font-size: 40px; line-height: 44px; letter-spacing: -.01em;
  font-variant-numeric: tabular-nums; color: var(--sr-ink); }
.sr-readout.sr-muted { color: var(--sr-amber); }
.sr-gauge-empty { width: 26px; height: 4px; border-radius: 2px; background: var(--sr-tertiary); margin-bottom: 10px; }
.sr-readout-lbl { font-size: 10px; letter-spacing: .05em; text-transform: uppercase;
  color: var(--sr-secondary); margin-top: 2px; }
.sr-gauge-legend { display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; margin-top: 4px; }
.sr-gauge-legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
  color: var(--sr-secondary); font-variant-numeric: tabular-nums; }
.sr-gauge-legend i { width: 9px; height: 9px; border-radius: 2px; }
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
  background: var(--sr-elevated); padding: 0 8px; font-style: normal; font-size: 12px; font-weight: 600;
  color: var(--sr-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }

/* small stat grid: coverage ring, exam, calibration */
.sr-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.sr-grid.sr-grid-2 { grid-template-columns: repeat(2, 1fr); }
@media (max-width: 560px) { .sr-grid { grid-template-columns: 1fr; } }
/* exam plan as its own row */
.sr-plan { display: flex; align-items: center; gap: 16px; }
.sr-plan-txt { flex: 1; min-width: 0; }
.sr-plan-status { font-size: 16px; font-weight: 600; color: var(--sr-ink); }
.sr-plan-detail { font-size: 13px; color: var(--sr-secondary); margin-top: 3px; line-height: 1.5; }
.sr-plan .sr-btn { flex: none; }
.sr-mini { display: flex; align-items: center; gap: 12px; }
.sr-ring { width: 56px; height: 56px; border-radius: 50%; flex: none;
  background: conic-gradient(var(--sr-coverage) var(--sr-ringval, 0deg), var(--sr-hairline) 0);
  display: grid; place-items: center; }
.sr-ring > span { width: 42px; height: 42px; border-radius: 50%; background: var(--sr-elevated);
  display: grid; place-items: center; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-k { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-s { font-size: 12px; color: var(--sr-secondary); }
.sr-mini-edit { margin-left: auto; align-self: flex-start; border: none; background: none;
  padding: 2px 4px; font: 600 11.5px var(--sr-font); color: var(--sr-accent); cursor: pointer; }
.sr-mini-edit:hover { text-decoration: underline; }

/* next action — the single primary CTA of the panel, visually distinguished */
.sr-next { display: flex; align-items: center; gap: 14px;
  background: color-mix(in srgb, var(--sr-accent) 6%, var(--sr-elevated));
  border: 1px solid color-mix(in srgb, var(--sr-accent) 24%, var(--sr-hairline));
  border-left: 3px solid var(--sr-accent); }
.sr-next .sr-eyebrow { color: var(--sr-accent); }
.sr-next .sr-t { font-weight: 600; font-size: 16px; }
.sr-next .sr-d { font-size: 13px; color: var(--sr-secondary); margin-top: 3px; line-height: 1.5; }

/* actions */
.sr-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sr-btn { font: 500 13px var(--sr-font); color: var(--sr-ink); cursor: pointer;
  background: var(--sr-surface); border: 1px solid var(--sr-hairline); border-radius: var(--sr-radius-pill);
  padding: 7px 14px; transition: background .15s, border-color .15s, transform .15s; }
.sr-btn:hover { border-color: var(--sr-accent); }
.sr-btn:active { transform: scale(.98); }
/* primary CTA: filled accent, white (onSignal) text, pill, md shadow */
.sr-btn.sr-primary { background: var(--sr-accent); border-color: var(--sr-accent); color: #fff;
  font-weight: 600; box-shadow: var(--sr-shadow); }
.sr-btn.sr-primary:hover { background: color-mix(in srgb, #000 8%, var(--sr-accent));
  border-color: color-mix(in srgb, #000 8%, var(--sr-accent)); }
@media (prefers-reduced-motion: reduce) { .sr-btn { transition: none; } .sr-btn:active { transform: none; } }
.sr-toggle { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--sr-secondary);
  cursor: pointer; border: 1px solid var(--sr-hairline); border-radius: var(--sr-radius-pill); padding: 6px 12px; }
.sr-toggle[data-on="1"] { color: var(--sr-ink); border-color: var(--sr-accent); }
.sr-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--sr-hairline); }
.sr-toggle[data-on="1"] .sr-dot { background: var(--sr-perf); }

/* dashboard header (toolbar window) */
.sr-dash { margin-top: 28px; }
.sr-dash-head { margin: 0 2px 4px; }
.sr-dash-title { font-family: var(--sr-display); font-weight: 600; font-size: 30px; line-height: 34px;
  letter-spacing: -.02em; margin: 0; color: var(--sr-ink); }
.sr-dash-sub { margin: 4px 0 0; font-size: 14px; color: var(--sr-secondary); }

/* themed finished-deck screen */
.sr-finished { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.sr-finished-check { width: 52px; height: 52px; border-radius: 50%; margin-bottom: 6px;
  background: color-mix(in srgb, var(--sr-perf) 16%, transparent); color: var(--sr-perf);
  display: grid; place-items: center; font-size: 26px; font-weight: 700; }
.sr-finished-title { font-family: var(--sr-display); font-weight: 600; font-size: 24px; color: var(--sr-ink); }
.sr-finished-sub { margin: 4px 0 14px; font-size: 14px; color: var(--sr-secondary); line-height: 1.5; max-width: 46ch; }

/* deck-home banner */
.sr-banner { max-width: 760px; margin: 4px auto 18px; background: var(--sr-elevated);
  border: 1px solid var(--sr-hairline); border-radius: var(--sr-radius); box-shadow: var(--sr-shadow);
  padding: 14px 18px; display: flex; align-items: center; gap: 16px; }
.sr-banner .sr-lead { font-size: 30px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.sr-banner .sr-lead.sr-muted { font-size: 17px; color: var(--sr-amber); }
.sr-banner .sr-meta { flex: 1; }
.sr-banner .sr-meta b { font-size: 14px; } .sr-banner .sr-meta div { font-size: 12px; color: var(--sr-secondary); margin-top: 2px; }
.sr-chip { font-size: 11px; color: var(--sr-secondary); border: 1px solid var(--sr-hairline);
  border-radius: var(--sr-radius-pill); padding: 3px 9px; white-space: nowrap; }
"""

# Native-chrome reskin of Anki's own screens. Kept to safe properties (colour,
# font, radius, spacing) so it can't break layouts; the reviewer *card* content
# is never touched. Injected only when the opt-in `speedrunModernUi` toggle is on.
# Tokens are provided once per page by ``page_style`` (embed pages) or bundled by
# the standalone helpers below (toolbar / bottom bar / reviewer), so these bodies
# never re-declare the token block.
_RESKIN = """
body { background: var(--sr-canvas) !important; color: var(--sr-ink);
  font-family: var(--sr-font); -webkit-font-smoothing: antialiased; }
a { color: var(--sr-accent); }
button, .btn { font-family: var(--sr-font) !important;
  border-radius: var(--sr-radius-input) !important; cursor: pointer; }
/* deck list: calm rows on the canvas, hairline separators, subtle hover */
table.deck { border-collapse: collapse; }
table.deck td, tr.deck td { padding-top: 8px !important; padding-bottom: 8px !important;
  border-bottom: 1px solid var(--sr-hairline) !important; }
tr.deck:hover td { background: color-mix(in srgb, var(--sr-accent) 6%, transparent); }
tr.deck td a.deck { color: var(--sr-ink); font-weight: 500; }
/* the deck browser gear / collapse affordances read as tertiary until hover */
tr.deck td img { opacity: .7; }
tr.deck:hover td img { opacity: 1; }
/* overview: the big primary study action reads as the accent CTA */
button#study, .overview button {
  font-family: var(--sr-font) !important; border-radius: var(--sr-radius-pill) !important; }
button#study { background: var(--sr-accent) !important; color: #fff !important;
  border: none !important; padding: 9px 22px !important; box-shadow: var(--sr-shadow-sm); }
"""

_TOOLBAR_RESKIN = """
#header, .toolbar { background: var(--sr-canvas) !important;
  border-bottom: 1px solid var(--sr-hairline) !important; }
.hitem { font-family: var(--sr-font); color: var(--sr-secondary); }
.hitem:hover { color: var(--sr-ink); }
#speedrun { color: var(--sr-accent); font-weight: 600; }
"""

# Reviewer bottom bar chrome (the answer-bar surface behind the buttons). Safe:
# only the bar background/border + button typography; never the card webview.
_BOTTOMBAR_RESKIN = """
body { background: var(--sr-canvas) !important; font-family: var(--sr-font); }
#outer, .stat { color: var(--sr-secondary); }
button { font-family: var(--sr-font) !important; }
"""

# Reviewer *card* webview chrome. Deliberately minimal and NON-!important so any
# note/template styling (.card, #qa, body backgrounds) always wins the cascade
# (these rules come before the card's own <style>). No margins/reflow -> no
# layout shift. This only calms the area *behind* the card.
_REVIEWER_CHROME = """
html { background: var(--sr-canvas); }
"""

# Overrides for Anki's Svelte/SvelteKit pages (congrats, graphs, deck options).
# Injected via ``webview_did_inject_style_into_page`` (we cannot edit Svelte
# source). The token block adapts to ``.night-mode`` automatically; these rules
# only retint page-level surfaces, never functional widgets.
_SVELTE_OVERRIDES = """
body, .container, .page { font-family: var(--sr-font); }
.night-mode body, body { background: var(--sr-canvas); }
"""


def _tokens_css() -> str:
    """The @font-face + light/dark custom-property block (no <style> wrapper)."""
    return f"{_TOKENS}\n:root {{ --sr-font:{_FONT_STACK}; --sr-display:{_DISPLAY_STACK}; }}"


def page_style() -> str:
    """Tokens + component CSS for a whole page, wrapped in one <style> tag.

    Injected **once per page** (via ``webview_will_set_content`` head or a
    ``stdHtml(head=...)`` call), so the token block is never duplicated by the
    per-embed builders below and never collides with the reskin's own copy.
    """
    return f"<style>{_TOKENS}{_COMPONENTS}</style>"


# Backwards-compatible alias. Builders no longer embed this (the page injects it
# once); kept so any external caller still resolves.
def component_style() -> str:
    """Deprecated alias of :func:`page_style` (kept for compatibility)."""
    return page_style()


def screen_reskin() -> str:
    """Deck-browser / overview reskin body. Tokens are already on the page via
    :func:`page_style`, so this carries no token block (avoids duplication)."""
    return f"<style>{_RESKIN}</style>"


def toolbar_reskin() -> str:
    """Top-toolbar reskin (a standalone webview: bundles its own tokens)."""
    return f"<style>{_tokens_css()}{_TOOLBAR_RESKIN}</style>"


def bottombar_reskin() -> str:
    """Reviewer bottom-bar chrome + answer buttons (standalone webview)."""
    return f"<style>{_tokens_css()}{_BOTTOMBAR_RESKIN}{_ANSWER_BUTTONS}</style>"


def reviewer_chrome_css() -> str:
    """Minimal, non-destructive reviewer card-webview background (standalone)."""
    return f"<style>{_tokens_css()}{_REVIEWER_CHROME}</style>"


def svelte_page_css() -> str:
    """Token + surface CSS text for injecting into a Svelte page's <style>."""
    return f"{_tokens_css()}{_SVELTE_OVERRIDES}"


# Color-coded, rounded answer buttons for the reviewer bottom bar. Targets the
# native answer buttons by their data-ease attribute (chrome only, not the card),
# with the interval time stacked beneath each label. Rating colours come straight
# from the tokens (again=danger, hard=amber, good=performance, easy=accent).
_ANSWER_BUTTONS = """
button[data-ease] {
  font-family: var(--sr-font) !important;
  border-radius: var(--sr-radius-input) !important;
  border: 1px solid var(--sr-hairline) !important;
  background: var(--sr-surface) !important;
  color: var(--sr-ink) !important;
  padding: 8px 16px !important; margin: 0 4px !important; cursor: pointer;
  transition: box-shadow .15s, border-color .15s; }
button[data-ease]:hover { box-shadow: var(--sr-shadow-sm); }
button[data-ease] .nobold { display: block; margin-top: 2px; font-size: 11px;
  font-weight: 400; color: var(--sr-secondary); }
button[data-ease="1"] { border-color: color-mix(in srgb, var(--sr-danger) 55%, transparent) !important;
  color: var(--sr-danger) !important; }
button[data-ease="2"] { border-color: color-mix(in srgb, var(--sr-amber) 55%, transparent) !important;
  color: var(--sr-amber) !important; }
button[data-ease="3"] { border-color: color-mix(in srgb, var(--sr-perf) 55%, transparent) !important;
  color: var(--sr-perf) !important; }
button[data-ease="4"] { border-color: color-mix(in srgb, var(--sr-accent) 55%, transparent) !important;
  color: var(--sr-accent) !important; }
#ansbut { font-family: var(--sr-font) !important; border-radius: var(--sr-radius-input) !important;
  border: none !important; background: var(--sr-accent) !important; color: #fff !important;
  padding: 8px 22px !important; cursor: pointer; }
"""


def reskin_style(kind: str) -> str:
    """Back-compat entry point: 'toolbar' -> toolbar reskin, else screen reskin
    (with tokens, for callers that inject it standalone)."""
    if kind == "toolbar":
        return toolbar_reskin()
    return f"<style>{_tokens_css()}{_RESKIN}</style>"


def answer_buttons_css() -> str:
    """Token-coloured answer buttons for the reviewer bottom bar (standalone)."""
    return bottombar_reskin()


# Per-failure-mode presentation for the in-reviewer diagnosis cue (spec:
# "Diagnosis cue (kind-aware)"). token key + icon per mode; one presentation used
# everywhere. Keyed by ``Diagnosis.kind`` (1 memory / 2 reasoning / 3 passage /
# 4 test-taking). Colours resolve from the same palette (never rely on colour
# alone -- each carries a distinct icon + label too).
DIAGNOSIS_STYLE: dict[int, tuple[str, str]] = {
    1: ("memory", "\U0001f9e0"),  # memory gap  -> memory blue, brain
    2: ("reasoning", "\U0001f4a1"),  # reasoning   -> violet,      bulb
    3: ("passage", "\U0001f4c4"),  # passage     -> teal,        doc
    4: ("amber", "\u23f1\ufe0f"),  # test-taking -> warn amber,  timer
}


def diagnosis_cue_js(
    *, kind_key: str, icon: str, title: str, action: str, night: bool
) -> str:
    """A self-contained JS IIFE that renders the themed, kind-aware post-miss cue
    as a fixed toast in the reviewer webview.

    Colours are baked in (light/dark resolved in Python) so the cue is correct
    even when the reskin/tokens aren't present on the card page. It is a
    ``position:fixed`` overlay -> it never shifts card layout; it clears on the
    next question, on dismiss, or after a readable 12s timeout. "Practice later"
    queues the miss for the end-of-session round (via pycmd) rather than
    abandoning the review mid-card.
    """
    p = _DARK if night else _LIGHT
    accent = p[kind_key]
    surface = p["elevated"]
    tint = f"color-mix(in srgb, {accent} 16%, {surface})"
    css = f"""
#speedrun-diagnosis {{ position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%);
  z-index: 2147483646; display: flex; align-items: flex-start; gap: 12px;
  max-width: 440px; width: calc(100% - 40px); box-sizing: border-box;
  background: {surface}; color: {p["ink"]}; border: 1px solid {p["hairline"]};
  border-left: 3px solid {accent}; border-radius: 16px; box-shadow: {p["shadow_lg"]};
  padding: 14px 14px 14px 16px; text-align: left;
  font-family: {_FONT_STACK}; -webkit-font-smoothing: antialiased;
  animation: srDiagIn .18s ease both; }}
#speedrun-diagnosis * {{ box-sizing: border-box; }}
@keyframes srDiagIn {{ from {{ opacity: 0; transform: translate(-50%, 8px); }}
  to {{ opacity: 1; transform: translate(-50%, 0); }} }}
@media (prefers-reduced-motion: reduce) {{ #speedrun-diagnosis {{ animation: none; }} }}
#speedrun-diagnosis .sr-diag-icon {{ flex: none; width: 34px; height: 34px; border-radius: 50%;
  background: {tint}; color: {accent}; display: grid; place-items: center; font-size: 18px; }}
#speedrun-diagnosis .sr-diag-body {{ flex: 1; min-width: 0; }}
#speedrun-diagnosis .sr-diag-eyebrow {{ font-size: 11px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: {accent}; }}
#speedrun-diagnosis .sr-diag-title {{ font-size: 15px; font-weight: 600; margin-top: 2px; color: {p["ink"]}; }}
#speedrun-diagnosis .sr-diag-action {{ font-size: 13px; color: {p["secondary"]}; margin-top: 3px; line-height: 1.45; }}
#speedrun-diagnosis .sr-diag-actions {{ margin-top: 10px; }}
#speedrun-diagnosis .sr-diag-btn {{ font: 600 12px {_FONT_STACK}; cursor: pointer; border: none;
  background: {accent}; color: #fff; border-radius: 999px; padding: 6px 14px; }}
#speedrun-diagnosis .sr-diag-btn:hover {{ opacity: .92; }}
#speedrun-diagnosis .sr-diag-x {{ flex: none; background: transparent; border: none; cursor: pointer;
  color: {p["secondary"]}; font-size: 20px; line-height: 1; padding: 0 2px; }}
#speedrun-diagnosis .sr-diag-x:hover {{ color: {p["ink"]}; }}
"""
    action_html = (
        f'<div class="sr-diag-action">{escape(action)}</div>' if action else ""
    )
    inner = (
        f'<div class="sr-diag-icon">{icon}</div>'
        '<div class="sr-diag-body">'
        '<div class="sr-diag-eyebrow">Diagnosis</div>'
        f'<div class="sr-diag-title">{escape(title)}</div>'
        f"{action_html}"
        '<div class="sr-diag-actions">'
        '<button class="sr-diag-btn" data-sr-practice>Practice later</button>'
        "</div></div>"
        '<button class="sr-diag-x" data-sr-dismiss aria-label="Dismiss">&times;</button>'
    )
    return (
        "(function(){"
        "var sid='speedrun-diagnosis-style';"
        "var st=document.getElementById(sid);"
        "if(!st){st=document.createElement('style');st.id=sid;document.head.appendChild(st);}"
        f"st.textContent={json.dumps(css)};"
        "var old=document.getElementById('speedrun-diagnosis'); if(old){old.remove();}"
        "var d=document.createElement('div'); d.id='speedrun-diagnosis';"
        f"d.innerHTML={json.dumps(inner)};"
        "document.body.appendChild(d);"
        "var pb=d.querySelector('[data-sr-practice]');"
        "if(pb){pb.addEventListener('click',function(){pycmd('speedrun:practicelater'); d.remove();});}"
        "var cb=d.querySelector('[data-sr-dismiss]');"
        "if(cb){cb.addEventListener('click',function(){d.remove();});}"
        # Auto-dismiss after a readable delay so the cue never lingers over the
        # next card if the student sits on the answer for a while.
        "setTimeout(function(){if(document.getElementById('speedrun-diagnosis')===d){d.remove();}},12000);"
        "})();"
    )


# JS to clear the diagnosis cue (used when the next question is shown).
REMOVE_DIAGNOSIS_JS = (
    "(function(){var el=document.getElementById('speedrun-diagnosis');"
    "if(el){el.remove();}})();"
)


# --- Qt styling (dialogs + global chrome) -----------------------------------

# Speedrun's Qt dialogs (self-explain, practice, exam target, library,
# onboarding) are native QDialogs, and Anki's own chrome (menus, tables, inputs)
# is Qt too. Both are styled from the *same* ``_LIGHT``/``_DARK`` palette above,
# so there is no separate Qt colour list to keep in sync. The web dark hairline
# is translucent; Qt uses the opaque ``hairline`` value (QSS ignores rgba borders
# inconsistently).

# Product sans/display, matching the web + mobile identity.
SR_QT_FONT = _FONT_STACK
SR_QT_DISPLAY = _DISPLAY_STACK


def _qt(night: bool) -> dict[str, str]:
    """The Qt-facing palette for the requested mode (opaque hairline)."""
    return _DARK if night else _LIGHT


def resolved(key: str, night: bool = False) -> str:
    """A single token's hex value for the mode -- for callers that must bake a
    colour into inline styles (e.g. the reviewer overlay buttons, which can't
    rely on the --sr-* custom properties being present on the card page)."""
    palette = _DARK if night else _LIGHT
    return palette.get(key, palette["accent"])


def dialog_qss(night: bool = False) -> str:
    """Qt style sheet applying the Speedrun token palette to a QDialog tree.

    Property selectors let callers opt into roles: set ``srRole`` to
    ``"display"``/``"title"``/``"eyebrow"``/``"muted"`` on a QLabel, ``srPrimary``
    to ``"1"`` on a QPushButton, or ``srState`` to ``"correct"``/``"wrong"`` on a
    QRadioButton, to get the token treatments. Radii follow the spec (card 20,
    input 12, primary pill).
    """
    p = _qt(bool(night))
    return f"""
    QDialog {{ background: {p["canvas"]}; }}
    QDialog, QLabel, QRadioButton, QCheckBox, QComboBox, QSpinBox, QDateEdit,
    QPlainTextEdit, QPushButton {{
        color: {p["ink"]}; font-family: {SR_QT_FONT}; font-size: 13px;
    }}
    QLabel[srRole="display"] {{ font-family: {SR_QT_DISPLAY}; font-size: 22px; font-weight: 600; }}
    QLabel[srRole="readout"] {{ font-family: {SR_QT_DISPLAY}; font-size: 40px; font-weight: 600; }}
    QLabel[srRole="title"] {{ font-size: 16px; font-weight: 600; }}
    QLabel[srRole="eyebrow"] {{ color: {p["secondary"]}; font-size: 11px; font-weight: 600; }}
    QLabel[srRole="muted"] {{ color: {p["secondary"]}; }}
    QLabel[srRole="good"] {{ color: {p["perf"]}; font-weight: 600; }}
    QLabel[srRole="bad"] {{ color: {p["danger"]}; font-weight: 600; }}
    QLabel[srRole="warn"] {{ color: {p["amber"]}; font-weight: 600; }}
    QLabel[srRole="chip"] {{ color: {p["secondary"]}; border: 1px solid {p["hairline"]};
        border-radius: {_RADII["pill"]}; padding: 2px 10px; }}
    QRadioButton {{ padding: 7px 6px; border-radius: {_RADII["input"]}; }}
    QRadioButton[srState="correct"] {{ color: {p["perf"]}; font-weight: 600;
        background: color-mix(in srgb, {p["perf"]} 12%, transparent); }}
    QRadioButton[srState="wrong"] {{ color: {p["danger"]}; font-weight: 600;
        background: color-mix(in srgb, {p["danger"]} 12%, transparent); }}
    QComboBox, QSpinBox, QDateEdit, QPlainTextEdit {{
        background: {p["field"]}; border: 1px solid {p["hairline"]};
        border-radius: {_RADII["input"]}; padding: 6px 8px;
        selection-background-color: {p["accent"]}; selection-color: #fff;
    }}
    QComboBox:focus, QSpinBox:focus, QDateEdit:focus, QPlainTextEdit:focus {{
        border-color: {p["accent"]};
    }}
    QPushButton {{
        background: {p["surface"]}; border: 1px solid {p["hairline"]};
        border-radius: {_RADII["input"]}; padding: 8px 16px;
    }}
    QPushButton:hover {{ border-color: {p["accent"]}; }}
    QPushButton:disabled {{ color: {p["secondary"]}; }}
    QPushButton[srPrimary="1"] {{
        background: {p["accent"]}; color: #fff; border: none; font-weight: 600;
        border-radius: {_RADII["pill"]}; padding: 9px 20px;
    }}
    QPushButton[srPrimary="1"]:hover {{ background: color-mix(in srgb, #000 10%, {p["accent"]}); }}
    QProgressBar {{ border: none; background: {p["hairline"]}; border-radius: 3px;
        max-height: 4px; }}
    QProgressBar::chunk {{ background: {p["accent"]}; border-radius: 3px; }}
    QFrame[srCard="1"] {{
        background: {p["elevated"]}; border: 1px solid {p["hairline"]};
        border-radius: {_RADII["card"]};
    }}
    QFrame[srRole="divider"] {{
        background: {p["hairline"]}; max-height: 1px; border: none;
    }}
    """


def global_qss(night: bool = False) -> str:
    """App-wide Qt chrome (menus, tooltips, tables, inputs) from the same tokens.

    Appended to Anki's own stylesheet via ``style_did_init`` so the whole app,
    not just Speedrun's dialogs, reads as one product. Kept surgical: only
    surfaces the spec cares about, so it can't break Anki's functional widgets.
    """
    p = _qt(bool(night))
    return f"""
    QMenu {{ background: {p["surface"]}; color: {p["ink"]};
        border: 1px solid {p["hairline"]}; border-radius: {_RADII["input"]}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px; border-radius: 8px; }}
    QMenu::item:selected {{ background: color-mix(in srgb, {p["accent"]} 16%, transparent);
        color: {p["ink"]}; }}
    QMenu::separator {{ height: 1px; background: {p["hairline"]}; margin: 4px 8px; }}
    QMenuBar {{ background: {p["canvas"]}; color: {p["ink"]}; }}
    QMenuBar::item:selected {{ background: color-mix(in srgb, {p["accent"]} 16%, transparent); }}
    QToolTip {{ background: {p["surface"]}; color: {p["ink"]};
        border: 1px solid {p["hairline"]}; padding: 4px 8px; }}
    QHeaderView::section {{ background: {p["canvas"]}; color: {p["secondary"]};
        border: none; border-bottom: 1px solid {p["hairline"]}; padding: 6px 8px; }}
    QTableView {{ gridline-color: {p["hairline"]};
        selection-background-color: color-mix(in srgb, {p["accent"]} 22%, transparent);
        selection-color: {p["ink"]}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit,
    QPlainTextEdit, QTextEdit {{
        background: {p["field"]}; border: 1px solid {p["hairline"]};
        border-radius: {_RADII["input"]}; padding: 5px 8px;
        selection-background-color: {p["accent"]}; selection-color: #fff; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QDateEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p["accent"]}; }}
    """


# --- helpers ----------------------------------------------------------------


def _pct(x: float) -> int:
    return int(round(max(0.0, min(1.0, x)) * 100))


def _bar(frac: float, color: str) -> str:
    return f'<div class="sr-bar"><i style="width:{_pct(frac)}%;background:{color}"></i></div>'


def _bridge(data: dict) -> str:
    """Recall -> performance bridge. When recall (or performance) evidence is
    still thin, the numeric gap is meaningless, so we show a neutral 'gathering
    data' state and a plain-language caption instead of a misleading '-N pts'."""
    memory = data.get("memory", 0.0)
    performance = data.get("performance", 0.0)
    gap = data.get("gap", 0.0)
    memory_ok = bool(data.get("memory_ok"))
    perf_ok = bool(data.get("perf_ok"))
    m, p = _pct(memory), _pct(performance)
    g = int(round(gap * 100))
    # A recall floor: below this there is essentially nothing recalled yet, so a
    # "transfer" verdict is meaningless -- a negative gap here is just the absence
    # of recall, not evidence of strong application. (Without this, 0% recall vs
    # 21% performance rendered "-21 pts / strong transfer", which is nonsense.)
    low_recall = 10  # percent

    recall_disp = f"{m}%" if memory_ok else "\u2014"
    perf_disp = f"{p}%" if perf_ok else "\u2014"
    if not memory_ok:
        span = '<div class="sr-span"><em>gathering recall data</em></div>'
        caption = (
            "Not enough graded reviews yet to measure recall, so the "
            "memory-to-application gap isn\u2019t meaningful. Keep reviewing to "
            "unlock it."
        )
    elif m < low_recall:
        span = '<div class="sr-span"><em>building recall</em></div>'
        caption = (
            f"Recall is still near zero ({m}%), so there\u2019s nothing to "
            "transfer yet \u2014 keep reviewing to build retention before this "
            "compares against new-question performance."
        )
    elif not perf_ok:
        span = '<div class="sr-span"><em>gathering question data</em></div>'
        caption = (
            f"You recall {m}% of the facts. Answer more held-out exam-style "
            "questions to measure whether that transfers to new questions."
        )
    elif g > 2:
        span = f'<div class="sr-span"><em>{g:+d} pts</em></div>'
        caption = (
            f"You recall {m}% of the facts but apply only {p}% on new exam-style "
            f"questions \u2014 a {g}-point gap. That gap is the bridge to close: "
            "it needs application practice, not more flashcards."
        )
    elif g < -2:
        span = f'<div class="sr-span"><em>{g:+d} pts</em></div>'
        caption = (
            f"You apply {p}% on new questions versus {m}% recall \u2014 strong "
            "transfer; keep broadening coverage."
        )
    else:
        span = f'<div class="sr-span"><em>{g:+d} pts</em></div>'
        caption = (
            "Recall and application are about even \u2014 what you remember is "
            "translating into new-question performance."
        )
    return (
        '<div class="sr-card"><p class="sr-eyebrow">Recall &rarr; performance bridge</p>'
        '<div class="sr-bridge">'
        f'<div class="sr-node"><b>{recall_disp}</b><span>Recall</span></div>'
        f"{span}"
        f'<div class="sr-node"><b>{perf_disp}</b><span>New questions</span></div>'
        "</div>"
        '<p style="margin:12px 0 0;font-size:13px;line-height:1.5;'
        f'color:var(--sr-secondary)">{caption}</p></div>'
    )


# --- banner (deck home) -----------------------------------------------------


def banner_html(data: dict) -> str:
    if data.get("sufficient"):
        lead = f'<div class="sr-lead">{data["readiness"]}</div>'
        meta = (
            f'<div class="sr-meta"><b>Projected MCAT readiness</b>'
            f"<div>Likely {data['low']}&ndash;{data['high']} &middot; "
            f"memory {_pct(data['memory'])}% &middot; performance {_pct(data['performance'])}%</div></div>"
        )
    else:
        lead = '<div class="sr-lead sr-muted">No score yet</div>'
        meta = (
            f'<div class="sr-meta"><b>Readiness withheld &mdash; not enough evidence</b>'
            f"<div>{escape(data.get('reason', ''))}</div></div>"
        )
    cov = f'<span class="sr-chip">{_pct(data["coverage"])}% covered</span>'
    return f'<div class="sr-banner">{lead}{meta}{cov}</div>'


# --- signature readiness instrument (panel/dashboard/finished) --------------

# Gauge geometry: concentric SVG rings on a 176x176 canvas. Each ring is a
# stroked <circle> rotated so 0% starts at 12 o'clock. Two *distinct* arcs
# (memory blue, performance green) -- never one blended gradient -- plus a thin
# outer coverage track and a low-high range band with a composite marker.
_GAUGE_C = 88  # centre
# Rings pushed outward to use the full 176px canvas, so the center is a clear
# ~44px-radius well: the score + "projected" label sit inside the innermost
# (performance) arc instead of colliding with it.
_R_COVER, _SW_COVER = 82, 3  # thin outer coverage track
_R_RANGE, _SW_RANGE = 72, 6  # readiness low-high band + marker
_R_MEM, _SW_MEM = 60, 9  # memory arc
_R_PERF, _SW_PERF = 48, 9  # performance arc


def _ring_track(r: int, sw: int) -> str:
    return (
        f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="{r}" fill="none" '
        f'stroke="var(--sr-hairline)" stroke-width="{sw}"/>'
    )


def _ring_arc(r: int, sw: int, color: str, frac: float) -> str:
    """A rounded arc from 12 o'clock clockwise covering ``frac`` of the circle."""
    circ = 2 * math.pi * r
    length = max(0.0, min(1.0, frac)) * circ
    return (
        f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="{r}" fill="none" stroke="{color}" '
        f'stroke-width="{sw}" stroke-linecap="round" '
        f'stroke-dasharray="{length:.2f} {circ - length:.2f}" '
        f'transform="rotate(-90 {_GAUGE_C} {_GAUGE_C})"/>'
    )


def _ring_segment(
    r: int, sw: int, color: str, start: float, end: float, *, round_: bool = False
) -> str:
    """A segment from ``start`` to ``end`` (fractions), for the range band/marker."""
    circ = 2 * math.pi * r
    start = max(0.0, min(1.0, start))
    end = max(0.0, min(1.0, end))
    if end < start:
        start, end = end, start
    seg = (end - start) * circ
    cap = ' stroke-linecap="round"' if round_ else ""
    # dash pattern: [0, gap-before, visible-seg, rest] -> reliable segment start.
    return (
        f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="{r}" fill="none" stroke="{color}" '
        f'stroke-width="{sw}"{cap} '
        f'stroke-dasharray="0 {start * circ:.2f} {seg:.2f} {circ:.2f}" '
        f'transform="rotate(-90 {_GAUGE_C} {_GAUGE_C})"/>'
    )


def _readiness_gauge(data: dict) -> str:
    """The signature instrument. Sufficient -> live arcs + range band + readout;
    otherwise a designed honest empty state (neutral tracks + amber em dash)."""
    rings: list[str] = []
    if data.get("sufficient"):

        def _frac(score: float) -> float:
            return max(0.0, min(1.0, (score - 472) / 56.0))

        comp = _frac(data["readiness"])
        low = _frac(data.get("low", data["readiness"]))
        high = _frac(data.get("high", data["readiness"]))
        rings.append(_ring_track(_R_COVER, _SW_COVER))
        rings.append(
            _ring_arc(_R_COVER, _SW_COVER, "var(--sr-coverage)", data["coverage"])
        )
        rings.append(_ring_track(_R_RANGE, _SW_RANGE))
        # low-high band (translucent accent) + a solid composite marker tick.
        rings.append(
            _ring_segment(
                _R_RANGE,
                _SW_RANGE,
                "color-mix(in srgb, var(--sr-accent) 28%, transparent)",
                low,
                high,
            )
        )
        rings.append(
            _ring_segment(
                _R_RANGE, _SW_RANGE, "var(--sr-accent)", comp - 0.007, comp + 0.007
            )
        )
        rings.append(_ring_track(_R_MEM, _SW_MEM))
        rings.append(_ring_arc(_R_MEM, _SW_MEM, "var(--sr-memory)", data["memory"]))
        rings.append(_ring_track(_R_PERF, _SW_PERF))
        rings.append(
            _ring_arc(_R_PERF, _SW_PERF, "var(--sr-perf)", data["performance"])
        )
        # A clean readout well: a card-colored disc just inside the innermost arc
        # so the score + label always rest on a clear surface (belt-and-suspenders
        # against any collision with the performance ring).
        rings.append(
            f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="42" fill="var(--sr-elevated)"/>'
        )
        center = (
            f'<span class="sr-readout">{data["readiness"]}</span>'
            '<span class="sr-readout-lbl">projected</span>'
        )
    else:
        # Honest empty state: a single dotted "pending" ring around a faint inner
        # disc, leaving a generous clear center so the label never collides with
        # an inner ring (the old design stacked four concentric tracks and the
        # label overlapped the innermost one).
        rings.append(
            f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="58" '
            'fill="var(--sr-elevated)"/>'
        )
        rings.append(
            f'<circle cx="{_GAUGE_C}" cy="{_GAUGE_C}" r="{_R_COVER}" fill="none" '
            'stroke="var(--sr-hairline)" stroke-width="4" stroke-linecap="round" '
            'stroke-dasharray="1.5 9"/>'
        )
        center = (
            '<span class="sr-gauge-empty"></span>'
            '<span class="sr-readout-lbl">No score yet</span>'
        )
    svg = (
        '<svg viewBox="0 0 176 176" role="img" aria-label="Readiness instrument">'
        f"{''.join(rings)}</svg>"
    )
    return (
        f'<div class="sr-gauge">{svg}<div class="sr-gauge-center">{center}</div></div>'
    )


def _hero(data: dict) -> str:
    gauge = _readiness_gauge(data)
    if data.get("sufficient"):
        legend = (
            '<div class="sr-gauge-legend">'
            f'<span><i style="background:var(--sr-memory)"></i>Memory {_pct(data["memory"])}%</span>'
            f'<span><i style="background:var(--sr-perf)"></i>Performance {_pct(data["performance"])}%</span>'
            f'<span><i style="background:var(--sr-coverage)"></i>Coverage {_pct(data["coverage"])}%</span>'
            "</div>"
        )
        return (
            '<div class="sr-card sr-herocard">'
            '<p class="sr-eyebrow">Readiness &middot; MCAT 472&ndash;528</p>'
            f"{gauge}{legend}"
            f'<p class="sr-range">Likely {data["low"]}&ndash;{data["high"]}</p>'
            f"<p>Updated {escape(data.get('updated', 'just now'))}.</p></div>"
        )
    # The gauge center already reads "no score yet", so strip the redundant
    # "not enough evidence:" prefix from the engine's reason and show only the
    # actionable detail as a single caption (no stacked, competing lines).
    reason = data.get("reason", "")
    prefix = "not enough evidence:"
    if reason.lower().startswith(prefix):
        reason = reason[len(prefix) :].strip()
        reason = reason[:1].upper() + reason[1:] if reason else reason
    reason_html = f'<p class="sr-abstain-reason">{escape(reason)}</p>' if reason else ""
    block = data.get("blocking", "")
    block_chip = (
        f'<span class="sr-block-chip">Weakest: {escape(block)}</span>'
        if block and block != "none"
        else ""
    )
    return (
        '<div class="sr-card sr-herocard sr-abstain">'
        '<p class="sr-eyebrow">Readiness &middot; MCAT 472&ndash;528</p>'
        f"{gauge}{reason_html}{block_chip}</div>"
    )


def _exam_row(data: dict) -> str:
    """The exam plan as its own full-width row with a clear Edit, so the plan
    reads as a first-class commitment rather than one tile among several."""
    exam = data.get("exam")
    if exam and exam.get("has"):
        if exam.get("readiness_sufficient"):
            status = (
                "On track — keep going"
                if exam.get("on_track")
                else f"Behind target by ~{exam.get('needed', 0)} pts"
            )
        else:
            status = "Plan set — gathering evidence"
        detail = (
            f"{exam.get('days_left', 0)} days to exam &middot; "
            f"{escape(exam.get('mode', ''))}"
        )
        if exam.get("readiness_sufficient") and not exam.get("on_track"):
            detail += f" &middot; need ~{exam.get('per_week', 0):.1f} pts/week"
        button = (
            '<button class="sr-btn" onclick="pycmd(\'speedrun:exam\')">Edit target</button>'
        )
    else:
        status = "No exam date set"
        detail = "Set your test date and target score to anchor the study plan."
        button = (
            '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:exam\')">'
            "Set target</button>"
        )
    return (
        '<div class="sr-card sr-plan"><div class="sr-plan-txt">'
        '<p class="sr-eyebrow">Exam plan</p>'
        f'<div class="sr-plan-status">{status}</div>'
        f'<div class="sr-plan-detail">{detail}</div></div>'
        f"{button}</div>"
    )


def _stats_row(data: dict) -> str:
    """Coverage + calibration as two compact tiles. The three signals live once
    in the hero gauge legend (no duplicate signal tiles), and per-topic coverage
    detail lives in the topic dashboard below."""
    ring_deg = _pct(data["coverage"]) * 3.6
    coverage = (
        '<div class="sr-card sr-mini">'
        f'<div class="sr-ring" style="--sr-ringval:{ring_deg}deg"><span>{_pct(data["coverage"])}%</span></div>'
        f'<div><div class="sr-k">{data["cov_covered"]}/{data["cov_total"]}</div>'
        '<div class="sr-s">topics covered</div></div></div>'
    )
    cal = data.get("calibration")
    if cal and cal.get("sufficient"):
        cal_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">'
            f"{cal.get('brier', 0):.2f}</div>"
            f'<div class="sr-s">Brier &middot; n={cal.get("n", 0)}</div></div></div>'
        )
    else:
        cal_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">&middot;&middot;&middot;</div>'
            '<div class="sr-s">calibration building</div></div></div>'
        )
    return f'<div class="sr-grid sr-grid-2">{coverage}{cal_html}</div>'


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


def _stack(
    data: dict,
    *,
    lead: str = "",
    panel_class: str = "sr-panel",
) -> str:
    """The one shared readiness stack, so the panel, dashboard, and finished
    screen can never drift. Order: the next-best-action leads (what to do now),
    then the hero instrument (whose legend is the single memory/performance/
    coverage readout), the recall->performance bridge, the exam plan as its own
    row, and a compact coverage+calibration pair. ``lead`` is optional content
    placed at the very top (a dashboard header or a finished-deck congrats card).
    Navigation and utility actions live in the persistent sidebar."""
    return (
        f'<div class="{panel_class}">'
        + lead
        + _next_action(data)
        + _hero(data)
        + _bridge(data)
        + _exam_row(data)
        + _stats_row(data)
        + "</div>"
    )


def panel_html(data: dict) -> str:
    """Full Speedrun panel for the per-deck overview. The token/component sheet
    is injected once per page by ``page_style`` (not embedded here)."""
    return _stack(data)


# --- topic-centric dashboard + per-topic drill-in ---------------------------

_TOPIC_KIND_CLASS = {
    "perf": "sr-k-perf",
    "memory": "sr-k-memory",
    "danger": "sr-k-danger",
    "amber": "sr-k-amber",
    "muted": "sr-k-muted",
}


def _pct_or_dash(v: float | None) -> str:
    """A percentage, or an en-dash when a signal has no evidence yet."""
    return f"{round(v * 100)}%" if v is not None else "–"


def _topic_row(t: dict) -> str:
    kind = _TOPIC_KIND_CLASS.get(t.get("kind", "muted"), "sr-k-muted")
    return (
        f"<button class=\"sr-trow\" onclick=\"pycmd('speedrun:topic:{escape(str(t['id']))}')\">"
        '<span class="sr-trow-main">'
        f'<span class="sr-trow-name">{escape(str(t.get("name", "")))}</span>'
        f'<span class="sr-trow-status {kind}">{escape(str(t.get("status", "")))}</span>'
        "</span>"
        '<span class="sr-trow-metrics">'
        f'<span class="sr-tmini" title="cards"><i style="background:var(--sr-coverage)"></i>{t.get("cards", 0)}</span>'
        f'<span class="sr-tmini" title="memory"><i style="background:var(--sr-memory)"></i>{_pct_or_dash(t.get("memory"))}</span>'
        f'<span class="sr-tmini" title="performance"><i style="background:var(--sr-perf)"></i>{_pct_or_dash(t.get("performance"))}</span>'
        "</span>"
        '<span class="sr-trow-chev">›</span>'
        "</button>"
    )


def _section_metrics(sec: dict) -> str:
    if sec.get("disabled"):
        return '<div class="sr-tsec-metrics"><span class="sr-tm">Passage practice</span></div>'
    return (
        '<div class="sr-tsec-metrics">'
        f'<span class="sr-tm"><i style="background:var(--sr-coverage)"></i>Coverage {round(sec.get("coverage", 0) * 100)}%</span>'
        f'<span class="sr-tm"><i style="background:var(--sr-memory)"></i>Memory {_pct_or_dash(sec.get("memory"))}</span>'
        f'<span class="sr-tm"><i style="background:var(--sr-perf)"></i>Perf {_pct_or_dash(sec.get("performance"))}</span>'
        "</div>"
    )


def _section_card(sec: dict) -> str:
    """One collapsed MCAT-section card (aggregate signals + a subtopic count),
    tappable to drill into that section's topics -- so the dashboard stays four
    cards instead of a 30-row wall."""
    head = (
        '<div class="sr-tsec-head"><div>'
        f'<div class="sr-tsec-name">{escape(str(sec.get("short", "")))}</div>'
        f'<div class="sr-tsec-full">{escape(str(sec.get("full", "")))}</div>'
        f"</div>{_section_metrics(sec)}</div>"
    )
    if sec.get("disabled"):
        return (
            f'<div class="sr-tsec disabled">{head}'
            '<div class="sr-tsec-empty">CARS is passage-based reading practice '
            "— no content-category cards.</div></div>"
        )
    n = len(sec.get("topics", []))
    foot = (
        '<div class="sr-tsec-foot">'
        f'<span>{n} topic{"s" if n != 1 else ""}</span>'
        '<span class="sr-trow-chev">›</span></div>'
    )
    return (
        f"<button class=\"sr-tsec sr-tsec-btn\" onclick=\"pycmd('speedrun:section:{escape(str(sec.get('key', '')))}')\">"
        f"{head}{foot}</button>"
    )


def _topic_sections_html(dash: dict) -> str:
    """The collapsed section cards (no heading), shared by the Home dashboard and
    the Decks-by-topic screen."""
    if not dash.get("has_topics"):
        return ""
    return "".join(
        _section_card(s)
        for s in dash.get("sections", [])
        if s.get("topics") or s.get("disabled")
    )


def topic_dashboard_html(dash: dict) -> str:
    """The MCAT-topic dashboard appended below the Home readiness stack: the four
    sections as collapsed cards (aggregate coverage/memory/performance), each
    tappable into its subtopics. Empty string when no topic map is loaded."""
    body = _topic_sections_html(dash)
    if not body:
        return ""
    return (
        '<div class="sr-topics"><div>'
        '<h2 class="sr-topics-title">MCAT topics</h2>'
        '<div class="sr-topics-sub">By MCAT section — tap a section to see its '
        "topics and their recall.</div></div>"
        f"{body}</div>"
    )


def section_detail_body(sec: dict) -> str:
    """One section's own page: its aggregate signals + the list of its subtopics
    (each tappable into the per-topic drill-in). Reached by tapping a section on
    Home / Decks, so those stay uncluttered."""
    rows = "".join(_topic_row(t) for t in sec.get("topics", []))
    if not rows:
        rows = (
            '<div class="sr-tsec-empty">No topics in your decks for this section '
            "yet — import the content library or group your cards.</div>"
        )
    return (
        '<div class="sr-topics">'
        "<button class=\"sr-pr-back\" onclick=\"pycmd('speedrun:decks')\">‹ All sections</button>"
        f'<div><h2 class="sr-topics-title">{escape(str(sec.get("short", "")))}</h2>'
        f'<div class="sr-topics-sub">{escape(str(sec.get("full", "")))}</div></div>'
        f'<div class="sr-tsec">{_section_metrics(sec)}{rows}</div>'
        "</div>"
    )


def decks_topic_body(dash: dict, ungrouped: int = 0) -> str:
    """The Decks screen, organized by MCAT topic instead of raw Anki decks: an
    optional 'group my cards' banner, the topic sections, and an escape hatch to
    the native deck list."""
    banner = ""
    if ungrouped > 0:
        banner = (
            '<div class="sr-card sr-next"><div style="flex:1">'
            '<p class="sr-eyebrow">Group your cards</p>'
            f'<div class="sr-t">{ungrouped} card{"s" if ungrouped != 1 else ""} not sorted into MCAT topics yet</div>'
            '<div class="sr-d">Auto-classify them so they appear under the right '
            "content categories.</div></div>"
            '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:group\')">Group by topic</button></div>'
        )
    sections = _topic_sections_html(dash)
    if not sections:
        sections = (
            '<div class="sr-card"><div class="sr-abstain-reason">No MCAT topics yet '
            "— import the content library, or group your own cards, to populate "
            "them.</div></div>"
        )
    return (
        '<div class="sr-topics"><div>'
        '<h2 class="sr-topics-title">Decks by MCAT topic</h2>'
        '<div class="sr-topics-sub">Your cards, organized by AAMC content area — '
        "tap a topic to study or check its recall.</div></div>"
        f"{banner}{sections}"
        "<button class=\"sr-btn\" style=\"margin-top:4px\" onclick=\"pycmd('speedrun:decks:all')\">"
        "All decks (New / Learn / Due)</button></div>"
    )


def _deck_row(d: dict) -> str:
    indent = int(d.get("depth", 0)) * 18
    new, learn, due = d.get("new", 0), d.get("learn", 0), d.get("review", 0)

    def cell(kind: str, n: int) -> str:
        nz = " nz" if n else ""
        return f'<span class="sr-deck-c sr-{kind}{nz}">{n}</span>'

    return (
        f"<button class=\"sr-deck-row\" onclick=\"pycmd('speedrun:deck:{int(d['id'])}')\">"
        f'<span class="sr-deck-name" style="padding-left:{indent}px">{escape(str(d.get("name", "")))}</span>'
        f'{cell("new", new)}{cell("learn", learn)}{cell("due", due)}'
        "</button>"
    )


def deck_list_body(data: dict) -> str:
    """A Speedrun-styled all-decks list (New / Learn / Due) with the deck actions
    on the bottom, replacing Anki's native deck browser + its native bottom bar."""
    rows = data.get("decks", [])
    sub = escape(str(data.get("studied", "Your decks, by New / Learn / Due.")))
    if rows:
        body = (
            '<div class="sr-deck-head"><span>Deck</span><span>New</span>'
            '<span>Learn</span><span>Due</span></div>'
            + "".join(_deck_row(d) for d in rows)
        )
    else:
        body = (
            '<div class="sr-deck-empty">No decks yet. Create one or import the MCAT '
            "content library to get started.</div>"
        )
    actions = (
        '<div class="sr-deck-actions">'
        "<button class=\"sr-btn sr-primary\" onclick=\"pycmd('speedrun:deck:create')\">Create deck</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:deck:import')\">Import file</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:lib:content')\">Import MCAT decks</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:deck:shared')\">Get shared</button>"
        "</div>"
    )
    return (
        '<div class="sr-alldecks"><div>'
        '<h2 class="sr-topics-title">All decks</h2>'
        f'<div class="sr-topics-sub">{sub}</div></div>'
        f'<div class="sr-deck-tbl">{body}</div>'
        f"{actions}</div>"
    )


def _tstat(value: str, label: str, sub: str, color_var: str) -> str:
    return (
        '<div class="sr-card">'
        f'<div class="sr-tstat-k" style="color:var({color_var})">{escape(value)}</div>'
        f'<div class="sr-tstat-lbl">{escape(label)}</div>'
        f'<div class="sr-tstat-sub">{escape(sub)}</div></div>'
    )


def topic_detail_body(t: dict) -> str:
    """One topic's own view: only this topic's three signals + actions, not the
    whole dashboard (reached by tapping a topic on Home / Decks)."""
    section = str(t.get("section") or "")
    weight = t.get("weight")
    eyebrow = " · ".join(
        x
        for x in [section.upper(), (f"WEIGHT {weight:g}" if weight else "")]
        if x
    )

    if t.get("review"):
        mem_v = _pct_or_dash(t.get("memory"))
        mem_sub = f"{t.get('mature', 0)} mature of {t.get('review', 0)} review cards"
    else:
        mem_v = "–"
        mem_sub = "No review cards yet — study these to build recall."

    if t.get("attempts"):
        perf_v = _pct_or_dash(t.get("performance"))
        perf_sub = f"{t.get('correct', 0)} of {t.get('attempts', 0)} questions correct"
    else:
        perf_v = "–"
        perf_sub = "No questions answered yet — practice to measure it."

    cov_sub = "cards in your library" if t.get("covered") else "not in your decks yet"

    tid = escape(str(t.get("id", "")))
    has_cards = bool(t.get("cards"))
    # Two paths from a topic: review its actual flashcards (memory) and practice
    # its exam-style questions (application). Review is the primary CTA when the
    # topic has cards; otherwise practice leads.
    review_btn = (
        f"<button class=\"sr-btn sr-primary\" onclick=\"pycmd('speedrun:topic:review:{tid}')\">"
        "Review memory cards</button>"
        if has_cards
        else ""
    )
    practice_cls = "sr-btn" if has_cards else "sr-btn sr-primary"
    practice_btn = (
        f"<button class=\"{practice_cls}\" onclick=\"pycmd('speedrun:topic:practice:{tid}')\">"
        "Practice questions</button>"
    )
    browse_btn = (
        f"<button class=\"sr-btn\" onclick=\"pycmd('speedrun:topic:study:{tid}')\">Browse cards</button>"
        if has_cards
        else ""
    )
    section_key = str(t.get("section_key") or "")
    back_cmd = f"speedrun:section:{section_key}" if section_key else "speedrun:decks"
    back_label = "‹ " + (section or "All topics")
    return (
        '<div class="sr-tdetail">'
        f"<button class=\"sr-pr-back\" onclick=\"pycmd('{back_cmd}')\">{escape(back_label)}</button>"
        f'<p class="sr-eyebrow">{escape(eyebrow)}</p>'
        f'<h1 class="sr-dash-title">{escape(str(t.get("name", "")))}</h1>'
        '<div class="sr-tstats">'
        + _tstat(str(t.get("cards", 0)), "Cards", cov_sub, "--sr-coverage")
        + _tstat(mem_v, "Memory", mem_sub, "--sr-memory")
        + _tstat(perf_v, "Performance", perf_sub, "--sr-perf")
        + "</div>"
        '<div class="sr-actions">'
        f"{review_btn}{practice_btn}{browse_btn}"
        "</div></div>"
    )


def finished_html(data: dict, deck_name: str) -> str:
    """Themed finished-deck screen: a calm congrats note + the readiness stack +
    clear paths back, replacing Anki's default congrats page (which otherwise
    hides the panel and dead-ends the user)."""
    congrats = (
        '<div class="sr-card sr-finished">'
        '<div class="sr-finished-check">\u2713</div>'
        f'<div class="sr-finished-title">{escape(deck_name)} \u2014 done for now</div>'
        '<p class="sr-finished-sub">No more cards due right now. Here\u2019s where you '
        "stand \u2014 pick your next move below.</p>"
        '<div class="sr-actions" style="justify-content:center">'
        '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:practice\')">Practice questions</button>'
        '<button class="sr-btn" onclick="pycmd(\'speedrun:dashboard\')">Open dashboard</button>'
        '<button class="sr-btn" onclick="pycmd(\'speedrun:decks\')">Back to decks</button>'
        "</div></div>"
    )
    # The congrats row already offers the primary Practice CTA (and Custom study
    # lives on the native overview bottom bar).
    return _stack(data, lead=congrats)


# --- in-place workspace (Dashboard / Practice / Settings as main-window tabs) --

_WORKSPACE_CSS = """
.sr-ws { max-width: 940px; margin: 0 auto; padding: 18px 20px 72px;
  font-family: var(--sr-font); color: var(--sr-ink); }
/* settings */
.sr-set-item { display:flex; align-items:flex-start; gap:20px; padding:18px 4px;
  border-bottom:1px solid var(--sr-hairline); }
.sr-set-item:last-child { border-bottom:none; }
.sr-set-txt { flex:1; min-width:0; }
.sr-set-title { font-size:16px; font-weight:600; color:var(--sr-ink); }
.sr-set-desc { font-size:13px; color:var(--sr-secondary); margin-top:5px; line-height:1.5; }
/* library rows: meta on the left, one action on the right */
.sr-lib-row { display:flex; align-items:center; gap:16px; padding:12px 0; }
.sr-lib-row + .sr-lib-row { border-top:1px solid var(--sr-hairline); }
.sr-lib-meta { flex:1; min-width:0; }
.sr-lib-row .sr-btn { flex:none; }
.sr-switch { flex:none; width:48px; height:28px; border-radius:999px; border:none; cursor:pointer;
  background:var(--sr-hairline); position:relative; transition:background .18s; margin-top:2px; }
.sr-switch.on { background:var(--sr-perf); }
.sr-switch i { position:absolute; top:3px; left:3px; width:22px; height:22px; border-radius:50%;
  background:#fff; box-shadow:var(--sr-shadow-sm); transition:left .18s; }
.sr-switch.on i { left:23px; }
/* practice */
.sr-pq-stem { font-family:var(--sr-display); font-size:22px; font-weight:600; line-height:1.35;
  margin:6px 0 18px; }
.sr-pq-opt { display:flex; align-items:center; gap:13px; width:100%; text-align:left;
  border:1px solid var(--sr-hairline); background:var(--sr-surface); color:var(--sr-ink);
  border-radius:var(--sr-radius-input); padding:14px 16px; margin:9px 0; font-size:15px;
  font-family:var(--sr-font); cursor:pointer; transition:border-color .12s, box-shadow .12s; }
.sr-pq-opt:hover { border-color:var(--sr-accent); }
.sr-pq-opt.sel { border-color:var(--sr-accent);
  box-shadow:0 0 0 2px color-mix(in srgb, var(--sr-accent) 32%, transparent); }
.sr-pq-opt.correct { border-color:var(--sr-perf);
  background:color-mix(in srgb, var(--sr-perf) 12%, var(--sr-surface)); }
.sr-pq-opt.wrong { border-color:var(--sr-danger);
  background:color-mix(in srgb, var(--sr-danger) 12%, var(--sr-surface)); }
.sr-pq-opt[disabled] { cursor:default; }
.sr-pq-letter { flex:none; width:26px; height:26px; border-radius:50%; border:1px solid var(--sr-hairline);
  display:inline-flex; align-items:center; justify-content:center; font-size:13px; font-weight:600;
  color:var(--sr-secondary); }
.sr-pq-row { display:flex; align-items:center; gap:10px; margin:16px 0 6px; color:var(--sr-secondary);
  font-size:14px; }
.sr-pq-row select { font-family:var(--sr-font); border:1px solid var(--sr-hairline);
  border-radius:var(--sr-radius-input); padding:8px 12px; background:var(--sr-field); color:var(--sr-ink); }
.sr-pq-explain { width:100%; box-sizing:border-box; min-height:66px; font-family:var(--sr-font);
  font-size:14px; border:1px solid var(--sr-hairline); border-radius:var(--sr-radius-input); padding:12px;
  background:var(--sr-field); color:var(--sr-ink); resize:vertical; margin:6px 0 4px; }
.sr-pq-verdict { font-family:var(--sr-display); font-size:22px; font-weight:600; margin:14px 0 6px; }
.sr-pq-verdict.good { color:var(--sr-perf); }
.sr-pq-verdict.bad { color:var(--sr-danger); }
.sr-pq-verdict.muted { color:var(--sr-secondary); }
.sr-pq-feedback { color:var(--sr-secondary); line-height:1.55; white-space:pre-line; }
.sr-pq-foot { display:flex; justify-content:flex-end; margin-top:22px; }
/* practice landing: MCAT section cards + drill-down */
.sr-pr-sections { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }
@media (max-width:560px) { .sr-pr-sections { grid-template-columns:1fr; } }
.sr-pr-sec { display:flex; flex-direction:column; gap:4px; text-align:left; width:100%;
  background:var(--sr-elevated); border:1px solid var(--sr-hairline);
  border-radius:var(--sr-radius); box-shadow:var(--sr-shadow); padding:18px 20px;
  cursor:pointer; font-family:var(--sr-font); transition:border-color .12s, box-shadow .12s; }
.sr-pr-sec:hover { border-color:var(--sr-accent); box-shadow:var(--sr-shadow-sm); }
.sr-pr-sec.disabled { cursor:default; }
.sr-pr-sec.disabled:hover { border-color:var(--sr-hairline); box-shadow:var(--sr-shadow); }
.sr-pr-sec-short { font-size:18px; font-weight:600; color:var(--sr-ink); }
.sr-pr-sec-full { font-size:12.5px; color:var(--sr-secondary); line-height:1.45; }
.sr-pr-sec-count { margin-top:6px; font-size:13px; font-weight:600; color:var(--sr-accent);
  font-variant-numeric:tabular-nums; }
.sr-pr-sec.disabled .sr-pr-sec-count { color:var(--sr-tertiary); }
.sr-pr-back { border:none; background:none; padding:0; margin:0 0 10px; cursor:pointer;
  font:600 13px var(--sr-font); color:var(--sr-accent); }
.sr-pr-back:hover { text-decoration:underline; }
/* inputs (sync) */
.sr-field-label { display:block; font-size:13px; font-weight:600; color:var(--sr-secondary);
  margin:14px 0 6px; }
.sr-inp { width:100%; box-sizing:border-box; font-family:var(--sr-font); font-size:15px;
  border:1px solid var(--sr-hairline); border-radius:var(--sr-radius-input); padding:11px 13px;
  background:var(--sr-field); color:var(--sr-ink); }
.sr-inp:focus { outline:none; border-color:var(--sr-accent);
  box-shadow:0 0 0 2px color-mix(in srgb, var(--sr-accent) 24%, transparent); }
.sr-sync-status { margin-top:14px; font-size:13px; color:var(--sr-secondary); }
/* sync pairing (Sync with phone) */
.sr-qr { display:flex; justify-content:center; padding:20px; background:#FFFFFF;
  border:1px solid var(--sr-hairline); border-radius:var(--sr-radius); }
.sr-qr svg { width:220px; height:220px; display:block; }
.sr-steps { margin:16px 4px; padding-left:22px; color:var(--sr-secondary); line-height:1.75;
  font-size:14.5px; }
.sr-steps b { color:var(--sr-ink); }
.sr-creds { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
  color:var(--sr-secondary); background:var(--sr-canvas); border:1px solid var(--sr-hairline);
  border-radius:var(--sr-radius-input); padding:12px 14px; margin-top:6px; word-break:break-all;
  line-height:1.7; }
.sr-creds b { color:var(--sr-ink); }
/* feedback report */
.sr-fb-score { display:flex; align-items:baseline; gap:10px; }
.sr-fb-sub { color:var(--sr-secondary); font-size:15px; }
.sr-fb-row { display:flex; align-items:center; gap:12px; padding:12px 2px;
  border-bottom:1px solid var(--sr-hairline); }
.sr-fb-row:last-child { border-bottom:none; }
.sr-fb-dot { width:10px; height:10px; border-radius:50%; flex:none; }
.sr-fb-name { flex:1; color:var(--sr-ink); font-weight:600; font-size:15px; }
.sr-fb-count { color:var(--sr-secondary); font-variant-numeric:tabular-nums; font-size:15px; }
.sr-chip { display:inline-block; background:var(--sr-canvas); border:1px solid var(--sr-hairline);
  border-radius:var(--sr-radius-pill); padding:6px 12px; margin:6px 6px 0 0; font-size:13px;
  color:var(--sr-ink); }
/* topic-centric dashboard (Home) */
.sr-topics { max-width:760px; margin:24px auto 0; display:flex; flex-direction:column; gap:14px; }
.sr-topics-title { font-family:var(--sr-display); font-weight:600; font-size:22px; letter-spacing:-.01em;
  margin:0; color:var(--sr-ink); }
.sr-topics-sub { font-size:13px; color:var(--sr-secondary); margin-top:2px; }
.sr-tsec { background:var(--sr-elevated); border:1px solid var(--sr-hairline); border-radius:var(--sr-radius);
  box-shadow:var(--sr-shadow); overflow:hidden; }
.sr-tsec-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; padding:16px 18px 13px; }
.sr-tsec-name { font-size:16px; font-weight:700; color:var(--sr-ink); }
.sr-tsec-full { font-size:12px; color:var(--sr-secondary); margin-top:2px; line-height:1.4; }
.sr-tsec-metrics { display:flex; flex-wrap:wrap; gap:12px; flex:none; justify-content:flex-end; }
.sr-tm { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--sr-secondary);
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.sr-tm i, .sr-tmini i { width:8px; height:8px; border-radius:2px; flex:none; display:inline-block; }
.sr-trow { display:flex; align-items:center; gap:12px; width:100%; text-align:left; background:none;
  border:none; border-top:1px solid var(--sr-hairline); padding:12px 18px; cursor:pointer;
  font-family:var(--sr-font); transition:background .12s; }
.sr-trow:hover { background:color-mix(in srgb, var(--sr-accent) 7%, transparent); }
.sr-trow-main { flex:1; min-width:0; display:flex; align-items:center; gap:10px; }
.sr-trow-name { font-size:14px; color:var(--sr-ink); font-weight:500; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.sr-trow-status { flex:none; font-size:11px; font-weight:600; padding:2px 9px; border-radius:var(--sr-radius-pill);
  color:var(--sr-secondary); background:var(--sr-canvas); }
.sr-trow-status.sr-k-perf { color:var(--sr-perf); background:color-mix(in srgb, var(--sr-perf) 15%, transparent); }
.sr-trow-status.sr-k-memory { color:var(--sr-memory); background:color-mix(in srgb, var(--sr-memory) 15%, transparent); }
.sr-trow-status.sr-k-danger { color:var(--sr-danger); background:color-mix(in srgb, var(--sr-danger) 15%, transparent); }
.sr-trow-status.sr-k-amber { color:var(--sr-amber); background:color-mix(in srgb, var(--sr-amber) 17%, transparent); }
.sr-trow-status.sr-k-muted { color:var(--sr-tertiary); background:var(--sr-canvas); }
.sr-trow-metrics { display:flex; gap:14px; flex:none; }
.sr-tmini { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--sr-secondary);
  font-variant-numeric:tabular-nums; min-width:44px; }
.sr-trow-chev { flex:none; color:var(--sr-tertiary); font-size:18px; line-height:1; }
.sr-tsec.disabled .sr-tsec-name { color:var(--sr-secondary); }
.sr-tsec-btn { display:block; width:100%; text-align:left; cursor:pointer; font-family:var(--sr-font);
  padding:0; transition:border-color .12s, box-shadow .12s; }
.sr-tsec-btn:hover { border-color:color-mix(in srgb, var(--sr-accent) 45%, var(--sr-hairline)); }
.sr-tsec-foot { display:flex; align-items:center; justify-content:space-between;
  padding:11px 18px; border-top:1px solid var(--sr-hairline); font-size:12.5px;
  font-weight:600; color:var(--sr-secondary); }
.sr-tsec-empty { padding:13px 18px; border-top:1px solid var(--sr-hairline); font-size:13px; color:var(--sr-tertiary); }
@media (max-width:560px) {
  .sr-tsec-head { flex-direction:column; }
  .sr-tsec-metrics { justify-content:flex-start; }
  .sr-trow-metrics { display:none; }
}
/* per-topic drill-in */
.sr-tdetail { max-width:760px; margin:0 auto; }
.sr-tstats { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:16px 0 20px; }
@media (max-width:560px) { .sr-tstats { grid-template-columns:1fr; } }
.sr-tstat-k { font-family:var(--sr-display); font-size:30px; font-weight:600; line-height:1.1;
  font-variant-numeric:tabular-nums; }
.sr-tstat-lbl { font-size:13px; font-weight:600; color:var(--sr-ink); margin-top:6px; }
.sr-tstat-sub { font-size:12px; color:var(--sr-secondary); margin-top:3px; line-height:1.45; }
/* all-decks list (Speedrun-styled replacement for the native deck browser) */
.sr-alldecks { max-width:760px; margin:0 auto; display:flex; flex-direction:column; gap:14px; }
.sr-deck-tbl { background:var(--sr-elevated); border:1px solid var(--sr-hairline);
  border-radius:var(--sr-radius); box-shadow:var(--sr-shadow); overflow:hidden; }
.sr-deck-head, .sr-deck-row { display:grid; grid-template-columns:1fr 52px 52px 52px; gap:8px;
  align-items:center; padding:12px 18px; }
.sr-deck-head { font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase;
  color:var(--sr-secondary); border-bottom:1px solid var(--sr-hairline); }
.sr-deck-head span:not(:first-child) { text-align:center; }
.sr-deck-row { width:100%; background:none; border:none; border-top:1px solid var(--sr-hairline);
  cursor:pointer; font-family:var(--sr-font); }
.sr-deck-row:hover { background:color-mix(in srgb, var(--sr-accent) 7%, transparent); }
.sr-deck-name { text-align:left; font-size:14px; color:var(--sr-ink); font-weight:500;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sr-deck-c { text-align:center; font-size:14px; font-variant-numeric:tabular-nums; color:var(--sr-tertiary); }
.sr-deck-c.sr-new.nz { color:var(--sr-memory); }
.sr-deck-c.sr-learn.nz { color:var(--sr-danger); }
.sr-deck-c.sr-due.nz { color:var(--sr-perf); }
.sr-deck-empty { padding:20px 18px; color:var(--sr-secondary); font-size:14px; line-height:1.6; }
.sr-deck-actions { display:flex; flex-wrap:wrap; gap:8px; }
"""


def screen_html(body: str) -> str:
    """Wrap a Speedrun screen (Home / Practice / Settings) for the main content
    area. Navigation lives in the persistent left sidebar, so the screen carries
    no tab bar or back button - just the centered content column."""
    return f'<style>{_WORKSPACE_CSS}</style><div class="sr-ws">{body}</div>'


def _sb_icon(paths: str) -> str:
    """A 24x24 line icon that inherits the nav item's color via currentColor."""
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        f"{paths}</svg>"
    )


# The rail is the single navigation surface. Grouped as: a primary cluster
# (Home / Decks / Practice / Library), a native-tools cluster that hands off to
# Anki's own dialogs (Add / Browse / Stats), then Settings. "decks" hands off to
# Anki's native deck flow; the Speedrun screens render in the main webview.
_SB_NAV = (
    (
        (
            "home",
            "Home",
            _sb_icon('<path d="M3 10.6 12 3l9 7.6"/><path d="M5 9.4V21h14V9.4"/>'),
        ),
        (
            "decks",
            "Decks",
            _sb_icon(
                '<rect x="4" y="7" width="12.5" height="10" rx="2"/>'
                '<path d="M7.5 7V5.6A1.6 1.6 0 0 1 9.1 4H19a1.6 1.6 0 0 1 1.6 1.6V15'
                'A1.6 1.6 0 0 1 19 16.6h-2.5"/>'
            ),
        ),
        (
            "practice",
            "Practice",
            _sb_icon(
                '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.1"/>'
            ),
        ),
        (
            "library",
            "Library",
            _sb_icon(
                '<path d="M5 4.2h10.5a1 1 0 0 1 1 1V20H6a1 1 0 0 1-1-1z"/>'
                '<path d="M16.5 5.2a2 2 0 0 1 2 2V20"/>'
            ),
        ),
        (
            "progress",
            "Progress",
            _sb_icon(
                '<line x1="4" y1="20" x2="20" y2="20"/>'
                '<path d="M6.5 20v-5"/><path d="M12 20v-9"/><path d="M17.5 20v-3.5"/>'
            ),
        ),
    ),
    (
        (
            "add",
            "Add",
            _sb_icon(
                '<circle cx="12" cy="12" r="8.5"/>'
                '<line x1="12" y1="8.4" x2="12" y2="15.6"/>'
                '<line x1="8.4" y1="12" x2="15.6" y2="12"/>'
            ),
        ),
        (
            "browse",
            "Browse",
            _sb_icon(
                '<circle cx="11" cy="11" r="6.2"/>'
                '<line x1="20" y1="20" x2="15.6" y2="15.6"/>'
            ),
        ),
    ),
    (
        (
            "settings",
            "Settings",
            _sb_icon(
                '<line x1="4" y1="8.5" x2="20" y2="8.5"/>'
                '<circle cx="9" cy="8.5" r="2.4" fill="var(--sr-surface)"/>'
                '<line x1="4" y1="15.5" x2="20" y2="15.5"/>'
                '<circle cx="15" cy="15.5" r="2.4" fill="var(--sr-surface)"/>'
            ),
        ),
    ),
)


_SIDEBAR_CSS = """
* { box-sizing:border-box; }
/* Fully strip the native macOS button chrome (raised/bordered/shadowed look) so
   rail items read as flat rows, not floating cards. !important on appearance +
   an explicit border/shadow reset defeats the platform default; each class below
   re-adds only the fill/shape it wants. */
button { -webkit-appearance:none !important; appearance:none !important; margin:0;
  border:none; background:transparent; box-shadow:none; outline:none; font:inherit; }
html,body { height:100%; margin:0; background:var(--sr-canvas); }
.sr-sb { display:flex; flex-direction:column; height:100%; padding:14px 10px;
  font-family:var(--sr-font); border-right:1px solid var(--sr-hairline); }
.sr-sb-brand { display:flex; align-items:center; gap:9px; padding:8px 12px 18px; }
.sr-sb-logo { width:24px; height:24px; flex:none; color:var(--sr-accent);
  display:flex; align-items:center; justify-content:center; }
.sr-sb-logo svg { width:24px; height:24px; }
.sr-sb-word { font-family:var(--sr-display); font-size:19px; font-weight:600;
  color:var(--sr-ink); letter-spacing:-.01em; }
.sr-sb-nav { display:flex; flex-direction:column; gap:1px; }
.sr-sb-item { display:flex; align-items:center; gap:11px; width:100%; text-align:left;
  border:none; background:transparent; color:var(--sr-secondary); font-family:var(--sr-font);
  font-size:14px; font-weight:500; padding:8px 12px; border-radius:9px;
  cursor:pointer; transition:background .12s, color .12s; }
.sr-sb-item:hover { background:color-mix(in srgb, var(--sr-ink) 6%, transparent);
  color:var(--sr-ink); }
.sr-sb-item.active { background:color-mix(in srgb, var(--sr-accent) 13%, transparent);
  color:var(--sr-accent); font-weight:600; }
.sr-sb-item svg { flex:none; width:18px; height:18px; opacity:.9; }
.sr-sb-grouplabel { font-size:10.5px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  color:var(--sr-tertiary); padding:16px 12px 5px; }
.sr-sb-div { height:1px; background:var(--sr-hairline); margin:10px 12px; opacity:.7; }
.sr-sb-spacer { flex:1; min-height:12px; }
/* Compact footer utility rail: AI coach, appearance, and sync fold from three
   bulky stacked cards into one slim row of icon buttons. Status reads as a small
   dot on the icon; the full label + detail live in the button's tooltip. */
.sr-sb-utility { display:flex; justify-content:center; gap:6px; padding:9px 6px 2px;
  border-top:1px solid var(--sr-hairline); margin-top:6px; }
.sr-sb-ubtn { position:relative; width:48px; height:36px; display:flex; align-items:center;
  justify-content:center; border:none; background:transparent; color:var(--sr-secondary);
  border-radius:9px; cursor:pointer; transition:background .12s, color .12s; }
.sr-sb-ubtn:hover { background:color-mix(in srgb, var(--sr-ink) 6%, transparent); color:var(--sr-ink); }
.sr-sb-ubtn:disabled { opacity:.38; cursor:default; }
.sr-sb-ubtn svg { width:19px; height:19px; }
.sr-sb-ubtn.on { color:var(--sr-accent); }
.sr-sb-ubtn-dot { position:absolute; top:6px; right:10px; width:7px; height:7px; border-radius:50%;
  background:var(--sr-tertiary); box-shadow:0 0 0 2px var(--sr-canvas); }
.sr-sb-ubtn-dot.ok { background:var(--sr-perf); }
.sr-sb-ubtn-dot.syncing { background:var(--sr-amber); animation:sr-sb-pulse 1s ease-in-out infinite; }
.sr-sb-ubtn-dot.error { background:var(--sr-danger); }
@keyframes sr-sb-pulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }
"""


# Footer utility icons (18-19px, inherit currentColor). Sparkle = AI, sun/moon/
# half-disc = appearance, circular arrows = sync.
_IC_AI = (
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    '<path d="M12 2l1.7 6.3L20 10l-6.3 1.7L12 18l-1.7-6.3L4 10l6.3-1.7z"/></svg>'
)
_IC_SYNC = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 12a9 9 0 0 1-9 9 9 9 0 0 1-8.5-6"/>'
    '<path d="M3 12a9 9 0 0 1 9-9 9 9 0 0 1 8.5 6"/>'
    '<path d="M21 3v4h-4"/><path d="M3 21v-4h4"/></svg>'
)
_IC_THEME = {
    "light": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
        'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    ),
    "dark": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
        'stroke-linejoin="round" aria-hidden="true">'
        '<path d="M20 14.5A7.5 7.5 0 0 1 9.5 4 6.5 6.5 0 1 0 20 14.5z"/></svg>'
    ),
    "system": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
        'aria-hidden="true"><circle cx="12" cy="12" r="8.2"/>'
        '<path d="M12 3.8a8.2 8.2 0 0 1 0 16.4z" fill="currentColor" stroke="none"/></svg>'
    ),
}


def _ai_button(ai: dict) -> str:
    """AI-coach toggle as a compact footer icon (sparkle). Accent + green dot when
    on, muted when off, disabled when the AI venv isn't installed. AI stays
    honestly optional - the app scores fully with it off."""
    if not ai.get("available"):
        return (
            '<button class="sr-sb-ubtn" disabled title="AI coach - unavailable">'
            f"{_IC_AI}</button>"
        )
    enabled = bool(ai.get("enabled"))
    on = " on" if enabled else ""
    dot = '<span class="sr-sb-ubtn-dot ok"></span>' if enabled else ""
    title = f"AI coach - {'On' if enabled else 'Off'} (click to toggle)"
    return (
        f'<button class="sr-sb-ubtn{on}" title="{escape(title)}" '
        "onclick=\"pycmd('speedrun:ai:toggle')\">"
        f"{_IC_AI}{dot}</button>"
    )


def _theme_button(mode: str) -> str:
    """Appearance as a single icon that shows the current mode and cycles
    System -> Light -> Dark on click (folds the old 3-wide segmented control)."""
    nxt = {"system": "light", "light": "dark", "dark": "system"}.get(mode, "light")
    label = {"system": "Auto", "light": "Light", "dark": "Dark"}.get(mode, "Auto")
    icon = _IC_THEME.get(mode, _IC_THEME["system"])
    title = f"Appearance - {label} (click to change)"
    return (
        f'<button class="sr-sb-ubtn" title="{escape(title)}" '
        f"onclick=\"pycmd('speedrun:theme:{nxt}')\">{icon}</button>"
    )


def _sync_button(sync: dict) -> str:
    """Phone-sync as a compact footer icon with a status dot; the label + detail
    (e.g. the LAN address) live in the tooltip instead of a bulky stacked chip."""
    state = escape(str(sync.get("state") or "idle"))
    label = str(sync.get("label") or "Sync with phone")
    detail = str(sync.get("detail") or "")
    dot_cls = state if state in ("ok", "syncing", "error") else ""
    title = f"{label} - {detail}" if detail else label
    return (
        f'<button class="sr-sb-ubtn" title="{escape(title)}" '
        "onclick=\"pycmd('speedrun:nav:sync')\">"
        f'{_IC_SYNC}<span class="sr-sb-ubtn-dot {dot_cls}"></span></button>'
    )


def sidebar_html(
    active: str,
    sync: dict | None = None,
    ai: dict | None = None,
    theme_mode: str = "system",
) -> str:
    """The persistent left-rail app shell: brand, primary navigation, and a
    compact footer utility row (AI-coach toggle, appearance cycle, and sync
    status) rendered as icon buttons. Rendered into its own webview so it
    survives every Anki state change (unlike content painted into the main
    webview)."""
    sync = sync or {}
    # Small uppercase section headers give the rail structure (one per nav group,
    # empty = no header). Falls back to a hairline divider between unlabeled groups.
    group_labels = ("", "Manage", "")
    parts: list[str] = []
    for group_index, group in enumerate(_SB_NAV):
        header = group_labels[group_index] if group_index < len(group_labels) else ""
        if header:
            parts.append(f'<div class="sr-sb-grouplabel">{escape(header)}</div>')
        elif group_index:
            parts.append('<div class="sr-sb-div"></div>')
        parts.extend(
            f'<div class="sr-sb-item{" active" if key == active else ""}" role="button" '
            f"tabindex=\"0\" onclick=\"pycmd('speedrun:nav:{key}')\">"
            f"{icon}<span>{item_label}</span></div>"
            for key, item_label, icon in group
        )
    items = "".join(parts)
    brand = (
        '<div class="sr-sb-brand"><span class="sr-sb-logo">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.6" stroke-linecap="round" aria-hidden="true">'
        '<line x1="12" y1="3.5" x2="12" y2="20.5"/>'
        '<line x1="4.2" y1="7.75" x2="19.8" y2="16.25"/>'
        '<line x1="4.2" y1="16.25" x2="19.8" y2="7.75"/></svg></span>'
        '<span class="sr-sb-word">Speedrun</span></div>'
    )
    ai_btn = _ai_button(ai) if ai is not None else ""
    theme_btn = _theme_button(theme_mode)
    sync_btn = _sync_button(sync)
    utility = (
        '<div class="sr-sb-utility" role="group" aria-label="Quick settings">'
        f"{ai_btn}{theme_btn}{sync_btn}</div>"
    )
    return (
        f"<style>{_SIDEBAR_CSS}</style>"
        f'<div class="sr-sb">{brand}'
        f'<nav class="sr-sb-nav">{items}</nav>'
        f'<div class="sr-sb-spacer"></div>'
        f"{utility}</div>"
    )


def feedback_report_body(fb: dict) -> str:
    """The end-of-session feedback report as tokened cards (replacing the plain
    text info box): a correct/total headline, misses grouped by cause with a
    color per dimension, and the weakest topics as chips."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Feedback report</h1>'
        '<p class="sr-dash-sub">Where your exam-style misses came from, so you '
        "know what to repair next.</p></div>"
    )
    total = int(fb.get("total", 0) or 0)
    if total == 0:
        empty = (
            '<div class="sr-card"><p style="color:var(--sr-secondary);line-height:1.6">'
            "No exam-style attempts recorded yet. Answer some held-out questions in "
            "Practice to build your report.</p></div>"
        )
        return f'<div class="sr-panel sr-dash">{header}{empty}</div>'
    correct = int(fb.get("correct", 0) or 0)
    pct = round(correct / total * 100) if total else 0
    summary = (
        '<div class="sr-card"><div class="sr-fb-score">'
        f'<span class="sr-readout" style="font-size:40px">{correct}/{total}</span>'
        f'<span class="sr-fb-sub">correct · {pct}%</span></div></div>'
    )
    kinds = (
        ("Memory", int(fb.get("memory", 0) or 0), "var(--sr-memory)"),
        ("Reasoning", int(fb.get("reasoning", 0) or 0), "var(--sr-reasoning)"),
        ("Passage", int(fb.get("passage", 0) or 0), "var(--sr-passage)"),
        ("Test-taking", int(fb.get("test_taking", 0) or 0), "var(--sr-amber)"),
    )
    rows = "".join(
        f'<div class="sr-fb-row"><span class="sr-fb-dot" style="background:{color}"></span>'
        f'<span class="sr-fb-name">{name}</span>'
        f'<span class="sr-fb-count">{cnt}</span></div>'
        for name, cnt, color in kinds
        if cnt
    )
    misses = (
        f'<div class="sr-card"><div class="sr-eyebrow">Misses by cause</div>{rows}</div>'
        if rows
        else ""
    )
    weak = [escape(str(t)) for t in (fb.get("weak_topics") or [])[:8]]
    weak_card = ""
    if weak:
        chips = "".join(f'<span class="sr-chip">{t}</span>' for t in weak)
        weak_card = f'<div class="sr-card"><div class="sr-eyebrow">Weakest topics</div>{chips}</div>'
    return f'<div class="sr-panel sr-dash">{header}{summary}{misses}{weak_card}</div>'


def sync_pair_body(data: dict) -> str:
    """The "Sync with phone" screen: USB pairing (recommended), QR, and LAN fallback."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Sync with phone</h1>'
        '<p class="sr-dash-sub">USB is the most reliable path on guest Wi-Fi. '
        "Plug in your phone, enable USB debugging, then pair once.</p></div>"
    )
    running = bool(data.get("running"))
    qr = data.get("qr_svg") or ""
    status = escape(data.get("status") or "")
    if running and qr:
        lan_url = escape(data.get("url") or "")
        usb_url = escape(data.get("usb_url") or "")
        user = escape(data.get("user") or "")
        token = escape(data.get("token") or "")
        usb_ready = bool(data.get("usb_ready"))
        usb_status = escape(data.get("usb_status") or "")
        usb_badge = (
            '<span style="color:var(--sr-perf);font-weight:600">USB tunnel active</span>'
            if usb_ready
            else '<span style="color:var(--sr-amber);font-weight:600">USB tunnel not ready</span>'
        )
        inner = (
            '<div class="sr-card" style="margin-bottom:14px;padding:16px 18px">'
            '<div class="sr-eyebrow">USB sync (recommended)</div>'
            "<ol class=\"sr-steps\">"
            "<li>Plug the phone into this Mac with a USB cable.</li>"
            "<li>On the phone, enable <b>USB debugging</b> and tap Allow.</li>"
            "<li>In Speedrun on the phone, tap <b>Sync via USB</b> and scan "
            "the QR below (or enter the USB server URL).</li></ol>"
            f"<p class=\"sr-sync-status\">{usb_badge}"
            + (f" - {usb_status}" if usb_status else "")
            + "</p>"
            f'<div class="sr-creds">Phone server URL: <b>{usb_url}</b><br>'
            f"user <b>{user}</b><br>key <b>{token}</b></div></div>"
            f'<div class="sr-qr">{qr}</div>'
            '<p class="sr-sync-status" style="margin-top:12px">'
            "The QR includes both USB and Wi-Fi URLs. On the phone, tap "
            "<b>Sync via USB</b> so it uses the USB address.</p>"
            '<details style="margin-top:14px;color:var(--sr-secondary)">'
            "<summary>Wi-Fi fallback (same network only)</summary>"
            f'<div class="sr-creds" style="margin-top:10px">'
            f"server <b>{lan_url}</b><br>user <b>{user}</b><br>key <b>{token}</b></div>"
            "</details>"
        )
        cta = "Sync now"
    else:
        inner = (
            '<p style="color:var(--sr-secondary);line-height:1.6">Start the local '
            "sync server to generate a pairing code your phone can scan. Your "
            "desktop stays the host, so it needs to be running to sync.</p>"
        )
        cta = "Start &amp; show code"
    actions = (
        '<div class="sr-actions" style="margin-top:18px">'
        f'<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:syncnow\')">{cta}</button>'
        "</div>"
    )
    if running:
        actions += (
            '<div class="sr-actions" style="margin-top:10px">'
            '<button class="sr-btn" onclick="pycmd(\'speedrun:syncusb\')">'
            "Refresh USB tunnel</button></div>"
            '<p class="sr-sync-status" style="margin-top:14px">'
            "Or pick which copy wins:</p>"
            '<div class="sr-actions" style="margin-top:10px">'
            '<button class="sr-btn" onclick="pycmd(\'speedrun:syncpull\')">'
            "Use phone data</button>"
            '<button class="sr-btn" onclick="pycmd(\'speedrun:syncpush\')">'
            "Use desktop data</button>"
            "</div>"
            '<p class="sr-sync-status" style="margin-top:18px">'
            "Testing sync again? Clear local study history, then pull from the phone.</p>"
            '<div class="sr-actions" style="margin-top:10px">'
            '<button class="sr-btn" style="border-color:var(--sr-danger);color:var(--sr-danger)" '
            "onclick=\"pycmd('speedrun:syncclear')\">"
            "Clear study data</button>"
            "</div>"
        )
    status_html = f'<p class="sr-sync-status">{status}</p>' if status else ""
    return (
        f'<div class="sr-panel sr-dash">{header}'
        f'<div class="sr-card">{inner}{actions}{status_html}</div></div>'
    )


def settings_body(items: list[dict], sync: dict | None = None) -> str:
    """The settings screen as clean HTML rows (title + description + a real
    toggle switch), replacing the cramped native dialog whose text collided.
    Includes a self-hosted Sync section (no AnkiWeb account)."""
    rows = "".join(
        '<div class="sr-set-item"><div class="sr-set-txt">'
        f'<div class="sr-set-title">{escape(it["title"])}</div>'
        f'<div class="sr-set-desc">{escape(it["desc"])}</div></div>'
        f'<button class="sr-switch{" on" if it["on"] else ""}" '
        f"onclick=\"pycmd('speedrun:set:{it['key']}')\"><i></i></button></div>"
        for it in items
    )
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Settings</h1>'
        '<p class="sr-dash-sub">Study levers and appearance. Changes apply '
        "immediately.</p></div>"
    )
    return (
        f'<div class="sr-panel sr-dash">{header}'
        f'<div class="sr-card" style="padding:2px 20px">{rows}</div>'
        f"{_sync_section(sync or {})}</div>"
    )


def _lib_row(title: str, desc_html: str, btn_label: str, cmd: str) -> str:
    return (
        '<div class="sr-lib-row"><div class="sr-lib-meta">'
        f'<div class="sr-set-title">{escape(title)}</div>'
        f'<div class="sr-set-desc">{desc_html}</div></div>'
        f'<button class="sr-btn sr-primary" onclick="pycmd(\'{escape(cmd)}\')">'
        f"{escape(btn_label)}</button></div>"
    )


def library_body(status: str, decks: list[dict]) -> str:
    """The content library as an inline screen (mirroring the other sidebar
    sections), replacing the old modal dialog: a guided e2e test, popular decks,
    the open-licensed question pack, and import-your-own. Buttons emit pycmds
    handled in speedrun.py; the native file/link pickers still open as OS
    dialogs, parented on the main window."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Library</h1>'
        f'<p class="sr-dash-sub">On this device: {escape(status)}</p></div>'
    )
    content = (
        '<div class="sr-card"><p class="sr-eyebrow">MCAT content library</p>'
        + _lib_row(
            "Open-licensed MCAT library",
            "186 source-cited flashcards + 124 practice questions across all 31 "
            "AAMC content categories (OpenStax, CC BY). Loads the coverage map.",
            "Add library",
            "speedrun:lib:content",
        )
        + "</div>"
    )
    sample = (
        '<div class="sr-card"><p class="sr-eyebrow">Demo</p>'
        + _lib_row(
            "Load sample study history",
            "Seeds mature review cards + practice attempts so your three scores "
            "(memory, performance, readiness) show with ranges right away. Clearly "
            "sample data - the score is still computed, not made up.",
            "Load sample",
            "speedrun:lib:sample",
        )
        + "</div>"
    )
    e2e = (
        '<div class="sr-card"><p class="sr-eyebrow">Guided end-to-end test</p>'
        + _lib_row(
            "Biology e2e test",
            "15 biology cards + 6 topic-matched questions. Review, finish, and "
            "the reasoning round pulls matched (not random) questions.",
            "Add e2e test",
            "speedrun:lib:e2e",
        )
        + "</div>"
    )
    deck_rows = "".join(
        _lib_row(
            deck["name"],
            f"{escape(deck['section'])} &middot; {escape(deck['size'])}",
            "Download & import",
            f"speedrun:lib:deck:{index}",
        )
        for index, deck in enumerate(decks)
    )
    popular = (
        f'<div class="sr-card"><p class="sr-eyebrow">Popular decks</p>{deck_rows}</div>'
    )
    mmlu = (
        '<div class="sr-card"><p class="sr-eyebrow">Practice questions</p>'
        + _lib_row(
            "MMLU MCAT-relevant pack",
            "Open-licensed held-out questions (MIT).",
            "Add pack",
            "speedrun:lib:mmlu",
        )
        + "</div>"
    )
    own = (
        '<div class="sr-card"><p class="sr-eyebrow">Import your own</p>'
        '<p class="sr-set-desc">.apkg / .colpkg decks, or .json / .csv question '
        "packs. Google Drive links work.</p>"
        '<div class="sr-actions" style="margin-top:12px">'
        '<button class="sr-btn" onclick="pycmd(\'speedrun:lib:pick\')">'
        "Choose file&hellip;</button>"
        '<button class="sr-btn" onclick="pycmd(\'speedrun:lib:paste\')">'
        "Paste a link&hellip;</button>"
        "</div></div>"
    )
    return (
        '<div class="sr-panel sr-dash">'
        f"{header}{content}{sample}{e2e}{popular}{mmlu}{own}</div>"
    )


def _sync_section(sync: dict) -> str:
    """A single pointer to the one-button "Sync with phone" flow, so sync has ONE
    home (the pairing screen, reached from here or the rail chip) rather than a
    duplicate manual URL/user/password form competing with the QR pairing."""
    status = escape(sync.get("status") or "")
    header = (
        '<div class="sr-dash-head" style="margin-top:26px">'
        '<h1 class="sr-dash-title" style="font-size:21px">Sync</h1>'
        '<p class="sr-dash-sub">Pair with your phone in one step \u2014 scan a QR, '
        "no AnkiWeb account. Reviews then flow both ways.</p></div>"
    )
    status_html = f'<div class="sr-sync-status">{status}</div>' if status else ""
    body = (
        '<div class="sr-card sr-lib-row"><div class="sr-lib-meta">'
        '<div class="sr-set-title">Sync with phone</div>'
        '<div class="sr-set-desc">Open the pairing screen to show the QR and '
        f"sync.</div>{status_html}</div>"
        '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:nav:sync\')">'
        "Open sync</button></div>"
    )
    return header + body


_PRACTICE_JS = """
<script>
window._srSel = (typeof window._srSel === 'undefined') ? null : window._srSel;
function srSel(el, i){
  window._srSel = i;
  var opts = document.querySelectorAll('.sr-pq-opt');
  for (var k=0;k<opts.length;k++){ opts[k].classList.remove('sel'); }
  el.classList.add('sel');
  var b = document.getElementById('sr-pq-submit'); if(b){ b.removeAttribute('disabled'); }
}
function srSubmit(){
  if(window._srSel===null){ return; }
  var conf = document.getElementById('sr-conf');
  var ex = document.getElementById('sr-explain');
  var payload = {sel: window._srSel, conf: conf?parseInt(conf.value):0, explain: ex?ex.value:''};
  window._srSel = null;
  pycmd('speedrun:pq:submit:'+encodeURIComponent(JSON.stringify(payload)));
}
</script>
"""


def _practice_landing_body(s: dict) -> str:
    """The Practice landing: a mixed-diagnostic quick start + the four MCAT
    section cards (with real question counts), so practice is organized around the
    exam instead of one flat random list."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Practice</h1>'
        '<p class="sr-dash-sub">Held-out, exam-style questions by MCAT section '
        "\u2014 these feed your performance signal and calibration.</p></div>"
    )
    total = int(s.get("total", 0))
    if total:
        quick = (
            '<div class="sr-card sr-next"><div style="flex:1">'
            '<p class="sr-eyebrow">Quick start</p>'
            '<div class="sr-t">Mixed diagnostic</div>'
            '<div class="sr-d">A spread of up to 20 questions across every section '
            "\u2014 good for a baseline.</div></div>"
            '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:pr:go:\')">'
            "Start</button></div>"
        )
    else:
        quick = (
            '<div class="sr-card" style="text-align:center;padding:40px 24px">'
            '<div class="sr-set-title">No practice questions yet</div>'
            '<p class="sr-set-desc" style="max-width:440px;margin:8px auto 18px">'
            "Import a question pack from the Library to start measuring performance "
            "separately from recall.</p>"
            '<button class="sr-btn sr-primary" onclick="pycmd(\'speedrun:library\')">'
            "Open Library</button></div>"
        )
    cards = ""
    for sec in s.get("sections", []):
        count = int(sec.get("count", 0))
        has_bank = bool(sec.get("subjects"))
        if not has_bank:
            # CARS: passage/reasoning practice, no discrete-question bank.
            cards += (
                '<div class="sr-pr-sec disabled">'
                f'<div class="sr-pr-sec-short">{escape(sec["short"])}</div>'
                f'<div class="sr-pr-sec-full">{escape(sec["full"])}</div>'
                '<div class="sr-pr-sec-count">Passage practice \u2014 from reading</div>'
                "</div>"
            )
        elif count:
            cards += (
                f'<button class="sr-pr-sec" onclick="pycmd(\'speedrun:pr:sec:{sec["key"]}\')">'
                f'<div class="sr-pr-sec-short">{escape(sec["short"])}</div>'
                f'<div class="sr-pr-sec-full">{escape(sec["full"])}</div>'
                f'<div class="sr-pr-sec-count">{count} question'
                f"{'s' if count != 1 else ''}</div></button>"
            )
        else:
            cards += (
                '<button class="sr-pr-sec" onclick="pycmd(\'speedrun:library\')">'
                f'<div class="sr-pr-sec-short">{escape(sec["short"])}</div>'
                f'<div class="sr-pr-sec-full">{escape(sec["full"])}</div>'
                '<div class="sr-pr-sec-count">No questions \u2014 import a pack</div>'
                "</button>"
            )
    grid = f'<div class="sr-pr-sections">{cards}</div>'
    return f'<div class="sr-panel sr-dash">{header}{quick}{grid}</div>'


def _practice_section_body(s: dict) -> str:
    """A section drill-down: practice the whole section, or one subject, each
    drawing topic-filtered questions from the bank."""
    sec = s["section"]
    count = int(s.get("count", 0))
    subjects = s.get("subjects", [])
    header = (
        '<div class="sr-dash-head">'
        '<button class="sr-pr-back" onclick="pycmd(\'speedrun:pr:home\')">'
        "\u2190 All sections</button>"
        f'<h1 class="sr-dash-title">{escape(sec["short"])}</h1>'
        f'<p class="sr-dash-sub">{escape(sec["full"])}</p></div>'
    )
    all_topics = ",".join(
        sub["subject"] for sub in subjects if int(sub.get("count", 0))
    )
    whole = ""
    if count and len(subjects) > 1:
        whole = _lib_row(
            "Practice whole section",
            f"{count} questions across {len(subjects)} subjects",
            "Start",
            f"speedrun:pr:go:{all_topics}",
        )
    rows = ""
    for sub in subjects:
        n = int(sub.get("count", 0))
        if n:
            rows += _lib_row(
                sub["label"],
                f"{n} question{'s' if n != 1 else ''}",
                "Start",
                f"speedrun:pr:go:{sub['subject']}",
            )
        else:
            rows += _lib_row(
                sub["label"],
                "No questions yet",
                "Import",
                "speedrun:library",
            )
    return (
        f'<div class="sr-panel sr-dash">{header}'
        f'<div class="sr-card">{whole}{rows}</div></div>'
    )


def practice_body(s: dict) -> str:
    """Render the in-place practice screen from a plain state dict built by
    speedrun.py. Dispatches on ``mode``: the section landing, a section
    drill-down, or the per-question runner (default)."""
    mode = s.get("mode", "runner")
    if mode == "landing":
        return _practice_landing_body(s)
    if mode == "section":
        return _practice_section_body(s)
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Practice</h1>'
        '<p class="sr-dash-sub">Held-out, exam-style questions \u2014 these feed '
        "your performance signal and calibration.</p></div>"
    )
    if s.get("empty"):
        body = (
            '<div class="sr-card" style="text-align:center;padding:44px 24px">'
            '<div class="sr-set-title">No practice questions yet</div>'
            '<p class="sr-set-desc" style="max-width:420px;margin:8px auto 18px">'
            "Import a question pack from the Content Library to start measuring "
            "performance separately from recall.</p>"
            '<button class="sr-btn sr-primary" '
            "onclick=\"pycmd('speedrun:library')\">Open Content Library</button></div>"
        )
        return f'<div class="sr-panel sr-dash">{header}{body}</div>'

    q = s["q"]
    answered = s["answered"]
    total = s["total"]
    idx = s["index"]
    progress = (
        f"Question {idx + 1} of {total}  \u00b7  "
        f"{escape(str(q['topic']).replace('_', ' '))}"
    )
    opts = ""
    for i, opt in enumerate(q["options"]):
        cls = "sr-pq-opt"
        attrs = f'onclick="srSel(this,{i})"'
        if answered:
            attrs = "disabled"
            if i == q["correct_index"]:
                cls += " correct"
            elif i == s.get("selected"):
                cls += " wrong"
        opts += (
            f'<button class="{cls}" {attrs}>'
            f'<span class="sr-pq-letter">{chr(65 + i)}</span>'
            f"<span>{escape(str(opt))}</span></button>"
        )

    inner = [
        f'<p class="sr-eyebrow">{progress}</p>',
        f'<div class="sr-pq-stem">{escape(str(q["stem"]))}</div>',
        opts,
    ]

    if not answered:
        inner.append(
            '<div class="sr-pq-row"><span>Confidence</span>'
            '<select id="sr-conf"><option value="0">(skip)</option>'
            '<option value="1">Low</option><option value="2">Medium</option>'
            '<option value="3">High</option></select></div>'
        )
        inner.append(
            '<textarea id="sr-explain" class="sr-pq-explain" '
            'placeholder="Self-explain your reasoning (optional)"></textarea>'
        )
        inner.append(
            '<div class="sr-pq-foot"><button id="sr-pq-submit" class="sr-btn sr-primary" '
            'disabled onclick="srSubmit()">Submit answer</button></div>'
        )
        inner.append(_PRACTICE_JS)
    else:
        vclass = s.get("verdict") or "muted"
        inner.append(
            f'<div class="sr-pq-verdict {vclass}">{escape(s.get("verdict_text", ""))}</div>'
        )
        if s.get("feedback"):
            inner.append(f'<div class="sr-pq-feedback">{escape(s["feedback"])}</div>')
        ai = s.get("ai")
        if ai:
            inner.append(_ai_card(ai))
        label = "Finish" if s.get("is_last") else "Next question"
        inner.append(
            '<div class="sr-pq-foot"><button class="sr-btn sr-primary" '
            f"onclick=\"pycmd('speedrun:pq:next')\">{escape(label)}</button></div>"
        )

    body = f'<div class="sr-card">{"".join(inner)}</div>'
    return f'<div class="sr-panel sr-dash">{header}{body}</div>'


def _ai_card(ai: dict) -> str:
    """The AI-coach region inside the practice verdict (spinner -> rationale +
    source citation), matching the old dialog's dedicated card."""
    inner = '<p class="sr-eyebrow">AI coach</p>'
    status = ai.get("status")
    if status:
        inner += f'<p class="sr-pq-feedback">{escape(status)}</p>'
    if ai.get("body"):
        inner += f'<p style="line-height:1.55;margin:4px 0">{escape(ai["body"])}</p>'
    if ai.get("source"):
        inner += (
            '<p style="margin-top:8px;font-size:12px;color:var(--sr-secondary)">'
            f"Source: {escape(ai['source'])}</p>"
        )
    return (
        '<div class="sr-card" style="margin-top:16px;'
        f'background:var(--sr-elevated)">{inner}</div>'
    )


def diagnostic_intro_body(count: int) -> str:
    """First-run placement quiz intro: a short, honest pitch plus Start / Skip.

    Rendered into the main webview right after onboarding (exam date + target),
    so the diagnostic seeds the signals before the user reaches the dashboard.
    """
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Quick placement check</h1>'
        '<p class="sr-dash-sub">A short, mixed set of exam-style questions across the '
        "MCAT sections. It seeds your performance and coverage signals so your "
        "dashboard starts from real evidence, not a blank slate.</p></div>"
    )
    body = (
        '<div class="sr-card sr-next"><div style="flex:1">'
        '<p class="sr-eyebrow">Placement quiz</p>'
        f'<div class="sr-t">{int(count)} questions, about 5 minutes</div>'
        '<div class="sr-d">Your answers are honest inputs to the three signals. '
        "Readiness stays provisional until it has enough evidence — this is a "
        "starting read, not a final score.</div></div></div>"
        '<div class="sr-pq-foot" style="gap:10px">'
        "<button class=\"sr-btn sr-primary\" onclick=\"pycmd('speedrun:diag:start')\">"
        "Start placement quiz</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:diag:skip')\">"
        "Skip for now</button></div>"
    )
    return f'<div class="sr-panel sr-dash">{header}{body}</div>'


def diagnostic_report_body(data: dict) -> str:
    """The placement result: overall + per-section accuracy bars + an honest
    readiness read (abstains until the evidence gate is met). Built from a plain
    dict by speedrun.py so it renders headlessly for previews too."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Your placement read</h1>'
        '<p class="sr-dash-sub">A first look at where you stand by section. This seeds '
        "performance and coverage; memory builds as you review, and readiness stays "
        "provisional until it has enough evidence.</p></div>"
    )
    overall = data.get("overall", {})
    ov = (
        '<div class="sr-card"><p class="sr-eyebrow">Overall</p>'
        f'<div class="sr-t">{int(overall.get("correct", 0))} / '
        f'{int(overall.get("total", 0))} correct '
        f'({int(overall.get("pct", 0))}%)</div></div>'
    )
    rows = ""
    for sec in data.get("sections", []):
        pct = int(sec.get("pct", 0))
        rows += (
            '<div style="margin:14px 0">'
            '<div style="display:flex;justify-content:space-between;font-weight:600">'
            f'<span>{escape(str(sec.get("short", "")))}</span>'
            f'<span>{int(sec.get("correct", 0))}/{int(sec.get("total", 0))} '
            f"· {pct}%</span></div>"
            '<div style="height:8px;border-radius:6px;background:var(--sr-elevated);'
            'margin-top:6px;overflow:hidden">'
            f'<div style="height:100%;width:{pct}%;background:var(--sr-perf)"></div>'
            "</div></div>"
        )
    sections = f'<div class="sr-card"><p class="sr-eyebrow">By section</p>{rows}</div>'
    r = data.get("readiness", {})
    if r.get("sufficient"):
        read = (
            f'<div class="sr-t">Initial readiness: {int(r.get("scaled", 0))}</div>'
            f'<div class="sr-d">Likely range {int(r.get("low", 0))}–'
            f'{int(r.get("high", 0))} on the MCAT scale.</div>'
        )
    else:
        read = (
            '<div class="sr-t">Readiness: building evidence</div>'
            f'<div class="sr-d">{escape(str(r.get("reason", "")))} Keep reviewing and '
            "practicing and a scored readiness will appear.</div>"
        )
    readiness = f'<div class="sr-card"><p class="sr-eyebrow">Readiness</p>{read}</div>'
    foot = (
        '<div class="sr-pq-foot" style="gap:10px">'
        "<button class=\"sr-btn sr-primary\" onclick=\"pycmd('speedrun:dashboard')\">"
        "Go to dashboard</button>"
        "<button class=\"sr-btn\" onclick=\"pycmd('speedrun:diag:start')\">"
        "Retake</button></div>"
    )
    return f'<div class="sr-panel sr-dash">{header}{ov}{sections}{readiness}{foot}</div>'


# --- Progress screen (charts) -----------------------------------------------
#
# A dedicated, chart-rich Progress screen (replacing the jump to Anki's native
# stats): three-signal tiles, a calibration reliability scatter, a misses-by-
# cause bar chart, and a coverage-map heatmap. Colors follow the signal/entity
# tokens (memory=blue, performance=green, coverage=gray; diagnosis kinds keep
# their fixed hues); text stays in ink tokens, one measure per axis.


def _progress_signal_tile(label: str, value: float, ok: bool, color: str) -> str:
    pct = _pct(value)
    shown = f"{pct}%" if ok else "thin"
    bar = color if ok else "var(--sr-tertiary)"
    return (
        '<div class="sr-card" style="flex:1;min-width:0">'
        f'<div style="font-size:30px;font-weight:700;color:var(--sr-ink)">{shown}</div>'
        f'<div class="sr-eyebrow" style="margin:2px 0 10px">{escape(label)}</div>'
        '<div style="height:6px;border-radius:4px;background:var(--sr-hairline);overflow:hidden">'
        f'<div style="height:100%;width:{pct}%;background:{bar}"></div></div></div>'
    )


def _reliability_svg(bins: list[dict]) -> str:
    w = h = 180
    pad = 22
    max_count = max((int(b.get("count", 0)) for b in bins), default=1) or 1
    dots = ""
    for b in bins:
        px = max(0.0, min(1.0, float(b.get("mean_predicted", 0.0))))
        py = max(0.0, min(1.0, float(b.get("mean_outcome", 0.0))))
        x = pad + px * (w - 2 * pad)
        y = (h - pad) - py * (h - 2 * pad)
        r = 3 + 6 * (int(b.get("count", 0)) / max_count)
        dots += (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="var(--sr-accent)" '
            f'fill-opacity="0.85"><title>predicted {px:.0%}, actual {py:.0%}, '
            f'n={int(b.get("count", 0))}</title></circle>'
        )
    diag = (
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{pad}" '
        'stroke="var(--sr-hairline)" stroke-width="1.5" stroke-dasharray="5 5"/>'
    )
    axes = (
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h - pad}" '
        'stroke="var(--sr-hairline)" stroke-width="1.5"/>'
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" '
        'stroke="var(--sr-hairline)" stroke-width="1.5"/>'
    )
    labels = (
        f'<text x="{w / 2:.0f}" y="{h - 4}" fill="var(--sr-tertiary)" font-size="10" '
        'text-anchor="middle">predicted</text>'
        f'<text x="11" y="{h / 2:.0f}" fill="var(--sr-tertiary)" font-size="10" '
        f'text-anchor="middle" transform="rotate(-90 11 {h / 2:.0f})">observed</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="200" height="200" role="img" '
        f'aria-label="Calibration reliability curve">{diag}{axes}{dots}{labels}</svg>'
    )


def _progress_calibration(cal: dict | None) -> str:
    head = '<div class="sr-eyebrow">Calibration — reliability curve</div>'
    if not cal or not cal.get("sufficient") or not cal.get("bins"):
        n = int((cal or {}).get("n", 0) or 0)
        note = (
            f"{n} predictions so far — a reliability curve appears once there "
            "are enough graded predictions to score calibration honestly."
            if n
            else "No graded predictions yet. Review cards and answer practice "
            "questions to build calibration."
        )
        return (
            f'<div class="sr-card">{head}'
            f'<p style="color:var(--sr-secondary);line-height:1.6;margin-top:8px">'
            f"{escape(note)}</p></div>"
        )
    stats = (
        '<div style="display:flex;gap:26px;margin-top:4px">'
        f'<div><div class="sr-readout" style="font-size:22px">{int(cal["n"])}</div>'
        '<div class="sr-eyebrow">predictions</div></div>'
        f'<div><div class="sr-readout" style="font-size:22px">{cal["brier"]:.3f}</div>'
        '<div class="sr-eyebrow">Brier</div></div>'
        f'<div><div class="sr-readout" style="font-size:22px">{cal["logloss"]:.3f}</div>'
        '<div class="sr-eyebrow">Log loss</div></div></div>'
    )
    caption = (
        '<p style="color:var(--sr-secondary);font-size:12px;margin-top:10px;line-height:1.5">'
        "Each dot is a probability bin: x = predicted recall, y = what actually "
        "happened. On the dashed diagonal means well-calibrated; dot size = how "
        "many predictions fell in that bin.</p>"
    )
    return (
        f'<div class="sr-card">{head}'
        '<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;margin-top:10px">'
        f'<div>{_reliability_svg(cal["bins"])}</div>'
        f'<div style="flex:1;min-width:170px">{stats}{caption}</div></div></div>'
    )


def _progress_misses(fb: dict) -> str:
    head = '<div class="sr-eyebrow">Misses by cause</div>'
    total = int(fb.get("total", 0) or 0)
    if total == 0:
        return (
            f'<div class="sr-card">{head}'
            '<p style="color:var(--sr-secondary);line-height:1.6;margin-top:8px">'
            "No exam-style attempts yet. Answer practice questions to see where "
            "your misses come from.</p></div>"
        )
    correct = int(fb.get("correct", 0) or 0)
    kinds = (
        ("Memory", "memory", "var(--sr-memory)"),
        ("Reasoning", "reasoning", "var(--sr-reasoning)"),
        ("Passage", "passage", "var(--sr-passage)"),
        ("Test-taking", "test_taking", "var(--sr-amber)"),
    )
    counts = [int(fb.get(key, 0) or 0) for _, key, _ in kinds]
    scale = max(counts + [1])
    rows = ""
    for (name, _key, color), cnt in zip(kinds, counts):
        width = int(round(100 * cnt / scale)) if scale else 0
        rows += (
            '<div style="display:flex;align-items:center;gap:10px;margin:9px 0">'
            f'<span style="width:96px;color:var(--sr-secondary);font-size:13px">{name}</span>'
            '<div style="flex:1;height:14px;border-radius:5px;background:var(--sr-hairline);overflow:hidden">'
            f'<div style="height:100%;width:{width}%;background:{color};border-radius:5px"></div></div>'
            '<span style="width:24px;text-align:right;color:var(--sr-ink);font-weight:600;'
            f'font-variant-numeric:tabular-nums">{cnt}</span></div>'
        )
    headline = (
        '<div style="margin-bottom:4px">'
        f'<span class="sr-readout" style="font-size:24px">{correct}/{total}</span> '
        f'<span style="color:var(--sr-secondary)">correct · {sum(counts)} misses</span></div>'
    )
    return f'<div class="sr-card">{head}{headline}{rows}</div>'


def _progress_coverage(d: dict) -> str:
    head = '<div class="sr-eyebrow">Coverage map</div>'
    topics = d.get("topics") or []
    if not topics:
        return (
            f'<div class="sr-card">{head}'
            '<p style="color:var(--sr-secondary);line-height:1.6;margin-top:8px">'
            "No topic outline loaded yet. Import the MCAT content library from the "
            "Library to build the coverage map.</p></div>"
        )
    total = int(d.get("cov_total", 0) or 0)
    covered = int(d.get("cov_covered", 0) or 0)
    weighted = _pct(d.get("weighted", 0.0))
    cells = "".join(
        f'<span title="{escape(str(t.get("label", "")))}" style="width:15px;height:15px;'
        f'border-radius:3px;background:'
        f'{"var(--sr-perf)" if t.get("covered") else "var(--sr-hairline)"}"></span>'
        for t in topics
    )
    grid = f'<div style="display:flex;flex-wrap:wrap;gap:5px;margin:12px 0">{cells}</div>'
    legend = (
        '<div style="display:flex;gap:18px;font-size:12px;color:var(--sr-secondary)">'
        '<span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;'
        'background:var(--sr-perf);vertical-align:middle;margin-right:5px"></span>covered</span>'
        '<span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;'
        'background:var(--sr-hairline);vertical-align:middle;margin-right:5px"></span>'
        "not yet</span></div>"
    )
    stat = (
        '<div style="margin-bottom:2px">'
        f'<span class="sr-readout" style="font-size:24px">{covered}/{total}</span> '
        f'<span style="color:var(--sr-secondary)">topics · {weighted}% weighted</span></div>'
    )
    weak = [escape(str(t)) for t in ((d.get("feedback") or {}).get("weak_topics") or [])[:8]]
    weak_html = ""
    if weak:
        chips = "".join(
            '<span style="background:var(--sr-elevated);border-radius:999px;padding:4px 11px;'
            f'font-size:12px;color:var(--sr-secondary)">{w}</span>'
            for w in weak
        )
        weak_html = (
            '<div style="margin-top:16px"><div class="sr-eyebrow" style="margin-bottom:8px">'
            "Weakest topics</div>"
            f'<div style="display:flex;flex-wrap:wrap;gap:6px">{chips}</div></div>'
        )
    return f'<div class="sr-card">{head}{stat}{grid}{legend}{weak_html}</div>'


def progress_body(d: dict) -> str:
    """The chart-rich Progress screen built from a plain dict (so it renders
    headlessly): three-signal tiles, a calibration reliability scatter, a
    misses-by-cause bar chart, and a coverage-map heatmap."""
    header = (
        '<div class="sr-dash-head"><h1 class="sr-dash-title">Progress</h1>'
        '<p class="sr-dash-sub">The three signals in depth: how well-calibrated '
        "your recall is, where your misses come from, and what the deck covers."
        "</p></div>"
    )
    signals = (
        '<div style="display:flex;gap:14px;margin-bottom:14px">'
        + _progress_signal_tile("Memory", d.get("memory", 0.0), d.get("memory_ok", True), "var(--sr-memory)")
        + _progress_signal_tile("Performance", d.get("performance", 0.0), d.get("perf_ok", True), "var(--sr-perf)")
        + _progress_signal_tile("Coverage", d.get("coverage", 0.0), True, "var(--sr-coverage)")
        + "</div>"
    )
    calib = _progress_calibration(d.get("calibration"))
    misses = _progress_misses(d.get("feedback") or {})
    coverage = _progress_coverage(d)
    return (
        '<div class="sr-panel sr-dash">'
        f"{header}{signals}{calib}{misses}{coverage}</div>"
    )
