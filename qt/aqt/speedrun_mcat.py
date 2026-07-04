# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""MCAT section/subject taxonomy shared by the Practice UI and its router.

Questions in the bank are tagged with subject strings (``biology``,
``biochemistry``, ``general_chemistry``, ``physics``, ``psychology_sociology``).
The MCAT itself is organized into four scored sections; this maps each section to
the subject tags it draws from, so Practice can present
section -> subject -> topic-filtered questions instead of one flat random list.

Kept separate from ``speedrun_theme`` (presentation) and ``speedrun`` (routing)
because both consume the same domain taxonomy.
"""

from __future__ import annotations

# (key, short label, full name, subject tags). CARS is passage/reasoning practice
# with no discrete-subject question bank, so it carries no subjects.
SECTIONS: list[dict] = [
    {
        "key": "chem_phys",
        "short": "Chem/Phys",
        "full": "Chemical & Physical Foundations of Biological Systems",
        "subjects": ["general_chemistry", "physics"],
    },
    {
        "key": "cars",
        "short": "CARS",
        "full": "Critical Analysis & Reasoning Skills",
        "subjects": [],
    },
    {
        "key": "bio_biochem",
        "short": "Bio/Biochem",
        "full": "Biological & Biochemical Foundations of Living Systems",
        "subjects": ["biology", "biochemistry"],
    },
    {
        "key": "psych_soc",
        "short": "Psych/Soc",
        "full": "Psychological, Social & Biological Foundations of Behavior",
        "subjects": ["psychology_sociology"],
    },
]

_SUBJECT_LABELS = {
    "biology": "Biology",
    "biochemistry": "Biochemistry",
    "general_chemistry": "General Chemistry",
    "physics": "Physics",
    "psychology_sociology": "Psychology / Sociology",
}


def section_by_key(key: str) -> dict | None:
    return next((s for s in SECTIONS if s["key"] == key), None)


def subject_label(subject: str) -> str:
    """A human label for a subject tag, falling back to a title-cased version so
    imported tags outside the known set still read cleanly."""
    return _SUBJECT_LABELS.get(subject, subject.replace("_", " ").title())


def section_key_for_subject(subject: str) -> str | None:
    """The section a subject tag belongs to (used to route imported questions)."""
    for section in SECTIONS:
        if subject in section["subjects"]:
            return section["key"]
    return None


def canonical_subject(text: str) -> str:
    """Normalize a free-text topic/subject cell from an import into a subject tag.

    Accepts the canonical keys, the display labels ("General Chemistry"), and
    loose punctuation ("Psychology/Sociology"); anything unknown is normalized to
    an underscore slug so it still imports and groups consistently.
    """
    slug = "_".join(text.strip().lower().replace("/", " ").replace("-", " ").split())
    if slug in _SUBJECT_LABELS:
        return slug
    for subject, label in _SUBJECT_LABELS.items():
        if slug == "_".join(label.lower().replace("/", " ").split()):
            return subject
    return slug
