# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun's source-grounded AI diagnosis coach (desktop, AI-optional).

The Rust core owns the deterministic classifier (the AI-off fallback and the
eval baseline). This package is the optional AI enrichment layer: it reads the
missed item's content + the student's chosen answer and classifies the
root-cause failure mode, grounded in the item's explanation (a named source),
with an explicit abstention when evidence is thin.
"""

from .taxonomy import Signals, deterministic_classify, keyword_classify, KIND_NAME
from .coach import diagnose

__all__ = [
    "Signals",
    "deterministic_classify",
    "keyword_classify",
    "KIND_NAME",
    "diagnose",
]
