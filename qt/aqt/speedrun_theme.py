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
_LIGHT: dict[str, str] = {
    "canvas": "#F2F3F5",
    "surface": "#FFFFFF",
    "elevated": "#FFFFFF",
    "ink": "#16181D",
    "secondary": "#6B7280",
    "tertiary": "#A2A8B0",
    "hairline_web": "#E7EAEE",
    "hairline": "#E7EAEE",
    "accent": "#2E7BF6",
    "memory": "#2E7BF6",
    "perf": "#22C55E",
    "coverage": "#8A94A6",
    "reasoning": "#7C5CFC",
    "passage": "#0E9AA7",
    "amber": "#E0900B",
    "danger": "#EF4444",
    "on_signal": "#16181D",
    "field": "#FFFFFF",
    "shadow_sm": "0 1px 2px rgba(0,0,0,.05)",
    "shadow": "0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06)",
    "shadow_lg": "0 12px 32px rgba(0,0,0,.10)",
}
_DARK: dict[str, str] = {
    "canvas": "#0C0D0F",
    "surface": "#17181B",
    "elevated": "#1E2024",
    "ink": "#F2F3F5",
    "secondary": "#9AA0A8",
    "tertiary": "#6B7280",
    "hairline_web": "rgba(255,255,255,.10)",
    "hairline": "#2A2D33",
    "accent": "#4B93FF",
    "memory": "#4B93FF",
    "perf": "#30D158",
    "coverage": "#6B7280",
    "reasoning": "#A78BFA",
    "passage": "#2DD4BF",
    "amber": "#FBBF24",
    "danger": "#FF6B6B",
    "on_signal": "#16181D",
    "field": "#202226",
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

/* honest abstention text */
.sr-abstain p { margin: 8px 0 0; font-size: 13px; color: var(--sr-secondary); line-height: 1.5; }
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
.sr-readout-lbl { font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
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
@media (max-width: 560px) { .sr-grid { grid-template-columns: 1fr; } }
.sr-mini { display: flex; align-items: center; gap: 12px; }
.sr-ring { width: 56px; height: 56px; border-radius: 50%; flex: none;
  background: conic-gradient(var(--sr-coverage) var(--sr-ringval, 0deg), var(--sr-hairline) 0);
  display: grid; place-items: center; }
.sr-ring > span { width: 42px; height: 42px; border-radius: 50%; background: var(--sr-elevated);
  display: grid; place-items: center; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-k { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sr-mini .sr-s { font-size: 12px; color: var(--sr-secondary); }

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
.sr-actions-spacer { flex: 1 1 auto; }
.sr-btn { font: 500 13px var(--sr-font); color: var(--sr-ink); cursor: pointer;
  background: var(--sr-surface); border: 1px solid var(--sr-hairline); border-radius: var(--sr-radius-pill);
  padding: 7px 14px; transition: background .15s, border-color .15s, transform .15s; }
.sr-btn:hover { border-color: var(--sr-accent); }
.sr-btn:active { transform: scale(.98); }
.sr-btn.sr-icon { padding: 7px 11px; font-size: 15px; line-height: 1; color: var(--sr-secondary); }
.sr-btn.sr-icon:hover { color: var(--sr-ink); }
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
    ``position:fixed`` overlay -> it never shifts card layout, and it does not
    auto-dismiss (removed on the next question or when dismissed), so it can be
    read. Offers an inline "Practice this" action routed via pycmd.
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
        '<button class="sr-diag-btn" data-sr-practice>Practice this</button>'
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
        "if(pb){pb.addEventListener('click',function(){pycmd('speedrun:practice'); d.remove();});}"
        "var cb=d.querySelector('[data-sr-dismiss]');"
        "if(cb){cb.addEventListener('click',function(){d.remove();});}"
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
    return (_DARK if night else _LIGHT).get(key, "#2E7BF6")


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


def _signal(name: str, frac: float, color: str, thin: bool) -> str:
    warn = '<div class="sr-thin">thin evidence</div>' if thin else ""
    return (
        f'<div class="sr-card sr-signal"><div class="sr-val">{_pct(frac)}%</div>'
        f'<div class="sr-name">{escape(name)}</div>{_bar(frac, color)}{warn}</div>'
    )


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

    recall_disp = f"{m}%" if memory_ok else "\u2014"
    perf_disp = f"{p}%" if perf_ok else "\u2014"
    if not memory_ok:
        span = '<div class="sr-span"><em>gathering recall data</em></div>'
        caption = (
            "Not enough graded reviews yet to measure recall, so the "
            "memory-to-application gap isn\u2019t meaningful. Keep reviewing to "
            "unlock it."
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
_R_COVER, _SW_COVER = 80, 3  # thin outer coverage track
_R_RANGE, _SW_RANGE = 68, 6  # readiness low-high band + marker
_R_MEM, _SW_MEM = 54, 9  # memory arc
_R_PERF, _SW_PERF = 40, 9  # performance arc


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
        center = (
            f'<span class="sr-readout">{data["readiness"]}</span>'
            '<span class="sr-readout-lbl">projected</span>'
        )
    else:
        # honest empty state: neutral tracks only, amber em dash in the hole.
        for r, sw in (
            (_R_COVER, _SW_COVER),
            (_R_RANGE, _SW_RANGE),
            (_R_MEM, _SW_MEM),
            (_R_PERF, _SW_PERF),
        ):
            rings.append(_ring_track(r, sw))
        center = (
            '<span class="sr-readout sr-muted">&mdash;</span>'
            '<span class="sr-readout-lbl">not enough evidence</span>'
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
    block = data.get("blocking", "")
    block_line = (
        f'<p>Weakest dimension right now: <span class="sr-block">{escape(block)}</span>.</p>'
        if block and block != "none"
        else ""
    )
    return (
        '<div class="sr-card sr-herocard sr-abstain">'
        '<p class="sr-eyebrow">Readiness &middot; MCAT 472&ndash;528</p>'
        f"{gauge}"
        f"<p>{escape(data.get('reason', ''))}</p>{block_line}</div>"
    )


def _signals(data: dict) -> str:
    return (
        '<div class="sr-signals">'
        + _signal(
            "Memory",
            data["memory"],
            "var(--sr-memory)",
            not data.get("memory_ok", True),
        )
        + _signal(
            "Performance",
            data["performance"],
            "var(--sr-perf)",
            not data.get("perf_ok", True),
        )
        + _signal("Coverage", data["coverage"], "var(--sr-coverage)", False)
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
            status = (
                "on track" if exam.get("on_track") else f"need +{exam.get('needed', 0)}"
            )
        else:
            status = "gathering evidence"
        exam_html = (
            '<div class="sr-card sr-mini"><div><div class="sr-k">'
            f"{exam.get('days_left', 0)}d</div>"
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
            f"{cal.get('brier', 0):.2f}</div>"
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


def _actions(data: dict, *, hide_practice: bool = False) -> str:
    """A secondary utility row. The single primary CTA is the Next Best Action
    above; these are all secondary so they never compete with it, and Practice is
    omitted here when it is already the recommended next step (no duplicate CTA)
    or when the surrounding screen already shows a Practice button
    (``hide_practice``, e.g. the finished screen's congrats row).
    Config toggles live behind Settings to keep the panel uncluttered."""
    na_cmd = (data.get("next_action") or {}).get("cmd")
    practice = ""
    if na_cmd != "speedrun:practice" and not hide_practice:
        practice = '<button class="sr-btn" onclick="pycmd(\'speedrun:practice\')">Practice questions</button>'
    seed = ""
    if data.get("cov_total", 0) == 0:
        seed = '<button class="sr-btn" onclick="pycmd(\'speedrun:seed\')">Seed MCAT topics</button>'
    exam_label = (
        "Edit exam target" if (data.get("exam") or {}).get("has") else "Set exam target"
    )
    return (
        '<div class="sr-actions">'
        f"{practice}"
        '<button class="sr-btn" onclick="pycmd(\'speedrun:library\')">Content library</button>'
        f"{seed}"
        f'<button class="sr-btn" onclick="pycmd(\'speedrun:exam\')">{exam_label}</button>'
        '<span class="sr-actions-spacer"></span>'
        '<button class="sr-btn sr-icon" title="Refresh" onclick="pycmd(\'speedrun:refresh\')">&#8635;</button>'
        '<button class="sr-btn sr-icon" title="Speedrun settings" onclick="pycmd(\'speedrun:settings\')">&#9881;</button>'
        "</div>"
    )


def _stack(
    data: dict,
    *,
    lead: str = "",
    panel_class: str = "sr-panel",
    hide_practice: bool = False,
) -> str:
    """The one shared readiness stack (hero instrument -> signals -> bridge ->
    mini grid -> next action -> actions), so the panel, dashboard, and finished
    screen can never drift. ``lead`` is optional content placed above the hero
    (a dashboard header or a finished-deck congrats card). ``hide_practice``
    drops the secondary Practice button when the lead already shows one."""
    return (
        f'<div class="{panel_class}">'
        + lead
        + _hero(data)
        + _signals(data)
        + _bridge(data)
        + _mini_grid(data)
        + _next_action(data)
        + _actions(data, hide_practice=hide_practice)
        + "</div>"
    )


def panel_html(data: dict) -> str:
    """Full Speedrun panel for the per-deck overview. The token/component sheet
    is injected once per page by ``page_style`` (not embedded here)."""
    return _stack(data)


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
    # lives on the native overview bottom bar), so drop the duplicate here.
    return _stack(data, lead=congrats, hide_practice=True)


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
/* inputs (sync) */
.sr-field-label { display:block; font-size:13px; font-weight:600; color:var(--sr-secondary);
  margin:14px 0 6px; }
.sr-inp { width:100%; box-sizing:border-box; font-family:var(--sr-font); font-size:15px;
  border:1px solid var(--sr-hairline); border-radius:var(--sr-radius-input); padding:11px 13px;
  background:var(--sr-field); color:var(--sr-ink); }
.sr-inp:focus { outline:none; border-color:var(--sr-accent);
  box-shadow:0 0 0 2px color-mix(in srgb, var(--sr-accent) 24%, transparent); }
.sr-sync-status { margin-top:14px; font-size:13px; color:var(--sr-secondary); }
"""


_SYNC_JS = """
<script>
function srSync(){
  var u=document.getElementById('sr-sync-url');
  var n=document.getElementById('sr-sync-user');
  var p=document.getElementById('sr-sync-pass');
  var payload={url:u?u.value:'', user:n?n.value:'', pass:p?p.value:''};
  pycmd('speedrun:sync:'+encodeURIComponent(JSON.stringify(payload)));
}
</script>
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


# (section key, label, icon). "study" hands off to Anki's native deck flow; the
# rest render as Speedrun screens in the main webview.
_SB_NAV = (
    (
        "home",
        "Home",
        _sb_icon('<path d="M3 10.6 12 3l9 7.6"/><path d="M5 9.4V21h14V9.4"/>'),
    ),
    (
        "study",
        "Study",
        _sb_icon(
            '<rect x="3" y="4.5" width="13" height="10" rx="2"/>'
            '<path d="M8 19h11a2 2 0 0 0 2-2V8.5"/>'
        ),
    ),
    (
        "practice",
        "Practice",
        _sb_icon('<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.1"/>'),
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
        "settings",
        "Settings",
        _sb_icon(
            '<line x1="4" y1="8.5" x2="20" y2="8.5"/>'
            '<circle cx="9" cy="8.5" r="2.4" fill="var(--sr-surface)"/>'
            '<line x1="4" y1="15.5" x2="20" y2="15.5"/>'
            '<circle cx="15" cy="15.5" r="2.4" fill="var(--sr-surface)"/>'
        ),
    ),
)


_SIDEBAR_CSS = """
* { box-sizing:border-box; }
html,body { height:100%; margin:0; background:var(--sr-surface); }
.sr-sb { display:flex; flex-direction:column; height:100%; padding:16px 12px;
  font-family:var(--sr-font); border-right:1px solid var(--sr-hairline); }
.sr-sb-brand { display:flex; align-items:center; gap:9px; padding:6px 10px 20px; }
.sr-sb-logo { width:22px; height:22px; border-radius:7px; flex:none;
  background:linear-gradient(135deg,var(--sr-accent),var(--sr-reasoning)); }
.sr-sb-word { font-family:var(--sr-display); font-size:19px; font-weight:600;
  color:var(--sr-ink); letter-spacing:-.01em; }
.sr-sb-nav { display:flex; flex-direction:column; gap:2px; }
.sr-sb-item { display:flex; align-items:center; gap:11px; width:100%; text-align:left;
  border:none; background:transparent; color:var(--sr-secondary); font-family:var(--sr-font);
  font-size:14.5px; font-weight:600; padding:10px 12px; border-radius:var(--sr-radius-input);
  cursor:pointer; transition:background .12s, color .12s; }
.sr-sb-item:hover { background:color-mix(in srgb, var(--sr-ink) 6%, transparent);
  color:var(--sr-ink); }
.sr-sb-item.active { background:color-mix(in srgb, var(--sr-accent) 14%, transparent);
  color:var(--sr-accent); }
.sr-sb-item svg { flex:none; width:18px; height:18px; }
.sr-sb-spacer { flex:1; }
.sr-sb-sync { display:flex; align-items:center; gap:10px; width:100%; text-align:left;
  border:1px solid var(--sr-hairline); background:var(--sr-surface); color:var(--sr-ink);
  border-radius:var(--sr-radius-input); padding:10px 12px; cursor:pointer; font-family:var(--sr-font);
  transition:border-color .12s, box-shadow .12s; }
.sr-sb-sync:hover { border-color:var(--sr-accent); box-shadow:var(--sr-shadow-sm); }
.sr-sb-sync-dot { flex:none; width:9px; height:9px; border-radius:50%; background:var(--sr-tertiary); }
.sr-sb-sync.ok .sr-sb-sync-dot { background:var(--sr-perf); }
.sr-sb-sync.syncing .sr-sb-sync-dot { background:var(--sr-amber);
  animation:sr-sb-pulse 1s ease-in-out infinite; }
.sr-sb-sync.error .sr-sb-sync-dot { background:var(--sr-danger); }
@keyframes sr-sb-pulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }
.sr-sb-sync-txt { display:flex; flex-direction:column; min-width:0; line-height:1.25; }
.sr-sb-sync-label { font-size:13.5px; font-weight:600; color:var(--sr-ink);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sr-sb-sync-detail { font-size:11.5px; color:var(--sr-secondary);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
"""


def sidebar_html(active: str, sync: dict | None = None) -> str:
    """The persistent left-rail app shell: brand, primary navigation, and a live
    sync status chip. Rendered into its own webview so it survives every Anki
    state change (unlike content painted into the main webview)."""
    sync = sync or {}
    items = "".join(
        f'<button class="sr-sb-item{" active" if key == active else ""}" '
        f"onclick=\"pycmd('speedrun:nav:{key}')\">{icon}<span>{label}</span></button>"
        for key, label, icon in _SB_NAV
    )
    state = escape(str(sync.get("state") or "idle"))
    label = escape(str(sync.get("label") or "Sync with phone"))
    detail = escape(str(sync.get("detail") or ""))
    detail_html = f'<span class="sr-sb-sync-detail">{detail}</span>' if detail else ""
    chip = (
        f'<button class="sr-sb-sync {state}" onclick="pycmd(\'speedrun:nav:settings\')">'
        '<span class="sr-sb-sync-dot"></span>'
        f'<span class="sr-sb-sync-txt"><span class="sr-sb-sync-label">{label}</span>'
        f"{detail_html}</span></button>"
    )
    brand = (
        '<div class="sr-sb-brand"><span class="sr-sb-logo"></span>'
        '<span class="sr-sb-word">Speedrun</span></div>'
    )
    return (
        f"<style>{_SIDEBAR_CSS}</style>"
        f'<div class="sr-sb">{brand}'
        f'<nav class="sr-sb-nav">{items}</nav>'
        f'<div class="sr-sb-spacer"></div>{chip}</div>'
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


def _sync_section(sync: dict) -> str:
    url = escape(sync.get("url") or "")
    user = escape(sync.get("username") or "")
    status = escape(sync.get("status") or "")
    placeholder = "http://192.168.1.20:27701/  (or your server's address)"
    header = (
        '<div class="sr-dash-head" style="margin-top:26px">'
        '<h1 class="sr-dash-title" style="font-size:21px">Sync</h1>'
        '<p class="sr-dash-sub">Sync this device with your phone through a '
        "self-hosted Anki sync server. Reviews flow both ways \u2014 no AnkiWeb "
        "account, and once you sign in here the toolbar Sync button uses it too."
        "</p></div>"
    )
    status_html = f'<div class="sr-sync-status">{status}</div>' if status else ""
    body = (
        '<div class="sr-card" style="padding:6px 20px 20px">'
        '<label class="sr-field-label">Server URL</label>'
        f'<input id="sr-sync-url" class="sr-inp" value="{url}" '
        f'placeholder="{escape(placeholder)}">'
        '<label class="sr-field-label">Username</label>'
        f'<input id="sr-sync-user" class="sr-inp" value="{user}" '
        'placeholder="demo">'
        '<label class="sr-field-label">Password</label>'
        '<input id="sr-sync-pass" class="sr-inp" type="password" '
        'placeholder="\u2022\u2022\u2022\u2022">'
        '<div class="sr-pq-foot"><button class="sr-btn sr-primary" '
        'onclick="srSync()">Sync now</button></div>'
        f"{status_html}</div>"
    )
    return header + body + _SYNC_JS


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


def practice_body(s: dict) -> str:
    """Render the in-place practice screen from a plain state dict built by
    speedrun.py (mirrors the old dialog's question/verdict/AI regions)."""
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
