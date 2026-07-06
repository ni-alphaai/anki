# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun content library + first-run onboarding for the desktop app.

Brings the mobile Library and onboarding flows to the desktop so the two apps
match:

- a curated popular-deck catalog with one-tap download + import (Google Drive
  share links are resolved past the virus-scan interstitial),
- one-tap import of the bundled, open-licensed MMLU practice pack,
- import-your-own (a file picker, or a pasted direct link),
- a first-run onboarding dialog (exam date + target tier) that seeds the exam
  profile via ``SetExamProfile``.

The catalog + import actions render inline through ``speedrun_theme.library_body``
(painted into the main webview by speedrun.py); only the exam-profile editor and
the native file/link pickers remain OS dialogs.

Everything runs through the existing ImportExport / SpeedrunService boundary.
There is no AI here; network access happens only when the user taps a button.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import aqt
from anki import import_export_pb2, speedrun_pb2
from aqt import speedrun_mcat as mcat
from aqt import speedrun_theme as theme
from aqt.qt import (
    QDate,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import disable_help_button, getFile, showWarning, tooltip

# Curated starter catalog, mirroring the mobile ``DeckCatalog.kt``. MileDown is
# the community "general content" staple on r/MCAT and imports with images.
_MILEDOWN_URL = (
    "https://drive.google.com/file/d/1K3Z2lbQIB_t_FhGq9wRp8IlXDC_shAzq/view?usp=sharing"
)
_POPULAR_DECKS = [
    {
        "name": "MileDown",
        "section": "Bio/Biochem, Chem/Phys & general content",
        "size": "~238 MB · includes images",
        "url": _MILEDOWN_URL,
    },
]

_MMLU_PACK = "speedrun_mmlu_pack.json"
_CARS_PACK = "speedrun_cars_pack.json"
_E2E_PACK = "speedrun_e2e_biology.json"
_PAYLOAD_KEYS = (
    "stem",
    "options",
    "correct_index",
    "explanation",
    "passage",
    "passage_id",
    "passage_title",
)

_CFG_ONBOARDED = "speedrunOnboarded"

# Shared dialog content margins, matching ``speedrun._DIALOG_MARGINS`` so every
# Speedrun dialog reads with the same gutter (radii unified via dialog_qss).
_DIALOG_MARGINS = (24, 22, 24, 20)


# --- styling helpers --------------------------------------------------------


def _night() -> bool:
    try:
        from aqt.theme import theme_manager

        return bool(theme_manager.night_mode)
    except Exception:
        return False


_W = TypeVar("_W", bound=QWidget)


def _mark(widget: _W, *, role: str | None = None, primary: bool = False) -> _W:
    if role is not None:
        widget.setProperty("srRole", role)
    if primary:
        widget.setProperty("srPrimary", "1")
    return widget


# --- bundled pack loading ---------------------------------------------------


def _bundled_pack_path(name: str) -> Path | None:
    """Locate a bundled JSON pack, both from source and in the installed app.

    Uses Anki's own data-folder resolver (which points at the packaged ``_aqt``
    data dir in an installer build, or the source tree during development), with
    a source-tree fallback.
    """
    candidates: list[Path] = []
    try:
        from aqt.utils import aqt_data_path

        base = aqt_data_path()
        candidates += [base / "web" / "imgs" / name, base / "speedrun" / name]
    except Exception:
        pass
    candidates.append(
        Path(aqt.__file__).resolve().parent / "data" / "web" / "imgs" / name
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_pack(name: str) -> dict | None:
    path = _bundled_pack_path(name)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _import_question_pack(col, pack: dict) -> int:
    """Register each question as a held-out item (mirrors import_question_pack)."""
    backend = col._backend
    imported = 0
    for question in pack.get("questions", []):
        card_id = 0
        tag = question.get("card_tag")
        if tag:
            cards = col.find_cards(f"tag:{tag}")
            if cards:
                card_id = cards[0]
        payload = json.dumps({k: question[k] for k in _PAYLOAD_KEYS if k in question})
        backend.add_question_item(
            speedrun_pb2.QuestionItem(
                card_id=card_id,
                topic=question.get("topic", ""),
                provenance=int(question.get("provenance", 0)),
                payload=payload,
            )
        )
        imported += 1
    return imported


# --- CSV question-bank import (UWorld/AAMC-style exports) --------------------
#
# A flexible column mapping so users can drop in their own exported question
# banks. Rows become the same payload shape as the JSON packs, then flow through
# _import_question_pack -> add_question_item.

_CSV_TOPIC_KEYS = ("subject", "topic", "section", "category")
_CSV_STEM_KEYS = ("stem", "question", "prompt", "question_text")
_CSV_CORRECT_KEYS = ("correct", "answer", "correct_index", "correct_answer", "key")
_CSV_EXPLANATION_KEYS = ("explanation", "rationale", "solution", "feedback")


def _csv_first(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""


def _csv_options(row: dict) -> list[str]:
    """Option cells as option_a..option_e, opt_a.., optiona.., or bare a..e."""
    options = []
    for letter in "abcde":
        for key in (f"option_{letter}", f"opt_{letter}", f"option{letter}", letter):
            value = str(row.get(key, "")).strip()
            if value:
                options.append(value)
                break
    return options


def _csv_correct_index(raw: str, options: list[str]) -> int | None:
    """A correct-answer cell as a letter (A-E), a 1-based number, or the answer
    text, resolved to a 0-based option index (None if it doesn't resolve)."""
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) == 1 and raw.isalpha():
        idx = ord(raw.upper()) - ord("A")
        return idx if 0 <= idx < len(options) else None
    if raw.isdigit():
        num = int(raw)
        if num == 0:
            return 0
        if 1 <= num <= len(options):
            return num - 1
    return options.index(raw) if raw in options else None


def _csv_to_pack(path: str) -> dict:
    """Parse a CSV of multiple-choice questions into the question-pack shape.

    Flexible headers: a subject/topic column, a stem/question column, option
    columns (option_a..option_e or bare a..e), a correct column (a letter, a
    1-based number, or the answer text), and an optional explanation column.
    """
    questions = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for raw_row in csv.DictReader(handle):
            row = {(k or "").strip().lower(): (v or "") for k, v in raw_row.items()}
            stem = _csv_first(row, _CSV_STEM_KEYS)
            options = _csv_options(row)
            index = _csv_correct_index(_csv_first(row, _CSV_CORRECT_KEYS), options)
            if not stem or len(options) < 2 or index is None:
                continue
            topic = _csv_first(row, _CSV_TOPIC_KEYS)
            questions.append(
                {
                    "topic": mcat.canonical_subject(topic) if topic else "",
                    "stem": stem,
                    "options": options,
                    "correct_index": index,
                    "explanation": _csv_first(row, _CSV_EXPLANATION_KEYS),
                    "provenance": 0,  # user-authored import
                }
            )
    return {"questions": questions}


# --- Google Drive download --------------------------------------------------


def _drive_file_id(url: str) -> str | None:
    if "drive.google.com" not in url and "drive.usercontent.google.com" not in url:
        return None
    match = re.search(r"/d/([^/]+)", url) or re.search(r"[?&]id=([^&]+)", url)
    return match.group(1) if match else None


def _resolve_download_url(url: str) -> str:
    """Turn a Drive share/view link into a direct, warning-skipping download."""
    file_id = _drive_file_id(url)
    if file_id:
        return (
            "https://drive.usercontent.google.com/download?"
            f"id={file_id}&export=download&confirm=t"
        )
    return url


def _parse_google_confirm(html: str) -> str | None:
    match = re.search(r'href="(/uc\?export=download[^"]+)"', html)
    if match:
        return "https://drive.google.com" + match.group(1).replace("&amp;", "&")
    match = re.search(r'action="(https://drive\.usercontent[^"]+)"', html)
    if match:
        return match.group(1).replace("&amp;", "&")
    return None


def _download_to(url: str, dest: str) -> None:
    """Download ``url`` to ``dest``, following Drive's confirm page if needed."""
    headers = {"User-Agent": "Mozilla/5.0 (Speedrun)"}
    target = _resolve_download_url(url)
    req = urllib.request.Request(target, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (user-initiated)
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype:
            confirmed = _parse_google_confirm(
                resp.read(200_000).decode("utf-8", "ignore")
            )
            if not confirmed:
                raise RuntimeError(
                    "This link needs a manual confirmation step in a browser."
                )
            req = urllib.request.Request(confirmed, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp2:  # noqa: S310
                _stream(resp2, dest)
            return
        _stream(resp, dest)


def _stream(resp, dest: str) -> None:
    with open(dest, "wb") as handle:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)


# --- import operations ------------------------------------------------------


def _import_options(col):
    try:
        return col._backend.get_import_anki_package_presets()
    except Exception:
        return import_export_pb2.ImportAnkiPackageOptions()


def _import_package_file(mw: aqt.AnkiQt, path: str) -> str:
    col = mw.col
    req = import_export_pb2.ImportAnkiPackageRequest(
        package_path=path, options=_import_options(col)
    )
    resp = col.import_anki_package(req)
    found = getattr(resp.log, "found_notes", 0)
    return f"Imported {found} notes." if found else "Deck imported."


def _run_with_progress(mw: aqt.AnkiQt, label: str, task, on_done) -> None:
    mw.progress.start(label=label, immediate=True)

    def done(future) -> None:
        mw.progress.finish()
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover - surfaced to the user
            showWarning(f"Speedrun: {exc}")
            return
        on_done(result)

    mw.taskman.run_in_background(task, done)


def download_and_import_deck(mw: aqt.AnkiQt, url: str, name: str) -> None:
    if mw.col is None:
        return

    def task() -> str:
        fd, tmp = tempfile.mkstemp(suffix=".apkg", prefix="speedrun_")
        os.close(fd)
        try:
            _download_to(url, tmp)
            return _import_package_file(mw, tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def on_done(msg: str) -> None:
        _refresh(mw)
        tooltip(f"{name}: {msg}")

    _run_with_progress(mw, f"Downloading and importing {name}…", task, on_done)


def import_local_file(mw: aqt.AnkiQt, path: str) -> None:
    if mw.col is None:
        return
    lower = path.lower()

    def task() -> str:
        if lower.endswith(".json"):
            pack = json.loads(Path(path).read_text(encoding="utf-8"))
            n = _import_question_pack(mw.col, pack)
            return f"Imported {n} practice questions."
        if lower.endswith(".csv"):
            n = _import_question_pack(mw.col, _csv_to_pack(path))
            return f"Imported {n} practice questions from CSV."
        return _import_package_file(mw, path)

    def on_done(msg: str) -> None:
        _refresh(mw)
        tooltip(msg)

    _run_with_progress(mw, "Importing…", task, on_done)


_CFG_EXAMPLE = "speedrunExampleLoaded"
_CFG_SAMPLE = "speedrunSampleSeeded"

# The open-licensed, per-topic MCAT content library (built by
# tools/build_content_library.py): a tagged flashcard deck across all 31 AAMC
# content categories plus matched practice questions, keyed by content-category
# id (1A..10A). Cards are tagged with their content-category id and subject so
# coverage attribution and the per-section practice bank both see them.
_CONTENT_PACK = "speedrun_content_library.json"
_CFG_CONTENT = "speedrunContentLibraryLoaded"
_CONTENT_DECK = "MCAT Content Library"


_topic_meta_cache: dict[str, dict] | None = None


def topic_meta() -> dict[str, dict]:
    """Map each content-category id (1A..10A) to its display metadata
    ``{name, section, subject, weight}`` from the bundled content library, so the
    topic dashboard can group the coverage/signal topics by MCAT section. Cached;
    returns an empty dict when the library isn't bundled."""
    global _topic_meta_cache
    if _topic_meta_cache is None:
        lib = _load_pack(_CONTENT_PACK) or {}
        topics = lib.get("topics", {})
        _topic_meta_cache = {
            str(cid): {
                "name": str(t.get("name", cid)),
                "section": str(t.get("section", "")),
                "subject": str(t.get("subject", "")),
                "weight": float(t.get("weight", 1.0)),
            }
            for cid, t in topics.items()
        }
    return _topic_meta_cache


def ensure_content_topic_map(col) -> bool:
    """Make the coverage topic map the 31 AAMC content categories (from the
    bundled library) so cards grouped by category id (1A..10E) actually attribute
    to a topic. Returns True if the map was applied. Cheap + idempotent."""
    lib = _load_pack(_CONTENT_PACK)
    if not lib:
        return False
    _load_content_topic_map(col, lib.get("topics", {}))
    return True


_CFG_TOPIC_MAP = "speedrunTopicMap"


def apply_topic_map_from_config(col) -> bool:
    """Apply a topic map synced via collection config (chunk sync does not carry
    ``sr_topic_map`` rows). Returns True when a non-empty map was applied."""
    try:
        raw = col.get_config(_CFG_TOPIC_MAP, None)
        if not raw:
            return False
        entries = [
            speedrun_pb2.TopicMapEntry(
                topic=str(item.get("topic", "")),
                label=str(item.get("label", "")),
                weight=float(item.get("weight", 1.0)),
            )
            for item in raw
            if item.get("topic")
        ]
        if not entries:
            return False
        col._backend.set_topic_map(entries)
        return True
    except Exception:
        return False


def refresh_readiness_after_sync(col) -> None:
    """Reconcile topic map + recompute readiness after a collection sync.

    Cards and ``sr_attempts`` sync via chunks, but ``sr_topic_map`` does not.
    Without this, the desktop can show 0% coverage and an abstaining readiness
    score even though review history arrived from the phone."""
    covered = 0
    try:
        covered = col._backend.get_coverage_report().topics_covered
    except Exception:
        pass
    if covered == 0:
        apply_topic_map_from_config(col)
        try:
            covered = col._backend.get_coverage_report().topics_covered
        except Exception:
            covered = 0
        if covered == 0:
            ensure_content_topic_map(col)
    try:
        col._backend.compute_readiness()
    except Exception:
        pass


def _load_content_topic_map(col, topics: dict) -> None:
    """Load the 31-category AAMC content outline as the coverage topic map, so
    coverage and readiness score against the real content categories the
    library's cards are tagged with, not just the coarse 10 foundational
    concepts. Persisted in the collection, so this only needs to run at import."""
    try:
        entries = [
            speedrun_pb2.TopicMapEntry(
                topic=cid,
                label=str(topic.get("name", cid)),
                weight=float(topic.get("weight", 1.0)),
            )
            for cid, topic in topics.items()
        ]
        if entries:
            col._backend.set_topic_map(entries)
    except Exception:
        pass


def _do_import_content_library(col) -> tuple[int, int] | None:
    """Import the open-licensed MCAT content library: a tagged flashcard deck
    across all 31 content categories plus the matched practice questions, and
    load the 31-category coverage map.

    Returns (cards_added, questions_added), (0, 0) if already imported, or None
    if the pack isn't bundled. Idempotent via a config flag; caller refreshes.
    """
    lib = _load_pack(_CONTENT_PACK)
    if lib is None:
        return None
    topics = lib.get("topics", {})
    _load_content_topic_map(col, topics)
    if col.get_config(_CFG_CONTENT, False):
        return (0, 0)
    did = col.decks.id(_CONTENT_DECK)
    model = col.models.by_name("Basic")
    cards_added = 0
    questions: list[dict] = []
    for cid, topic in topics.items():
        subject = str(topic.get("subject", ""))
        for card in topic.get("cards", []):
            note = col.new_note(model)
            note["Front"] = card.get("front", "")
            note["Back"] = card.get("back", "")
            note.tags = [tag for tag in (cid, subject) if tag]
            col.add_note(note, did)
            cards_added += 1
        for q in topic.get("questions", []):
            questions.append(
                {
                    "topic": subject,
                    "card_tag": cid,
                    "stem": q.get("stem", ""),
                    "options": q.get("options", []),
                    "correct_index": q.get("correct_index", 0),
                    "explanation": q.get("explanation", ""),
                    "provenance": 1,  # open_licensed
                }
            )
    n_q = _import_question_pack(col, {"questions": questions})
    col.set_config(_CFG_CONTENT, True)
    return (cards_added, n_q)


def import_content_library(mw: aqt.AnkiQt) -> None:
    """One-tap import of the full open-licensed MCAT content library (Library
    button). Idempotent; loads the 31-category coverage map either way."""
    col = mw.col
    if col is None:
        return
    if _load_pack(_CONTENT_PACK) is None:
        showWarning(
            "The content library isn't bundled with this build. Run "
            "tools/build_content_library.py, or import a .json pack via "
            "'Import your own'."
        )
        return
    try:
        result = _do_import_content_library(col)
    except Exception as exc:  # pragma: no cover - surfaced to the user
        showWarning(f"Speedrun: content library import failed: {exc}")
        return
    _refresh(mw)
    if result == (0, 0):
        tooltip("The MCAT content library is already imported.")
    elif result:
        tooltip(
            f"Imported {result[0]} cards + {result[1]} questions across the 31 "
            "MCAT content categories."
        )


def seed_sample_history(mw: aqt.AnkiQt) -> None:
    """Seed a labeled sample study history so all three scores show with ranges
    for a demo: mark existing cards as mature review cards and record exam-style +
    SRS attempts (with predictions), via the shared engine RPC. The readiness
    score stays COMPUTED from this seeded history, never hand-set - it is clearly
    surfaced as sample data."""
    col = mw.col
    if col is None:
        return
    # One-time: don't re-seed (and re-tooltip) on repeat clicks / restarts.
    if col.get_config(_CFG_SAMPLE, False):
        tooltip("Sample study history is already loaded.")
        return

    def task():
        # 0 => the engine's demo defaults (30 mature cards, 24 exam + 12 SRS).
        return col._backend.seed_sample_history(
            mature_cards=0, exam_attempts=0, srs_attempts=0
        )

    def on_done(resp) -> None:
        _refresh(mw)
        if getattr(resp, "cards_matured", 0) == 0:
            tooltip("Import a deck first, then load sample study history.")
            return
        col.set_config(_CFG_SAMPLE, True)
        tooltip(
            f"Loaded sample study history: {resp.cards_matured} mature cards + "
            f"{resp.attempts_recorded} attempts. Your three scores now show "
            "(sample data)."
        )

    _run_with_progress(mw, "Loading sample study history…", task, on_done)


def clear_study_data_for_sync_test(mw: aqt.AnkiQt, on_done=None) -> None:
    """Wipe Speedrun study history on this desktop so sync can be re-tested.

    Deletes practice attempts and readiness snapshots, resets matured sample
    cards back to new, and clears the sample-loaded flag. Imported decks and
    the topic map stay in place."""
    from anki.cards import CardId
    from aqt.utils import askUser

    col = mw.col
    if col is None:
        return
    if not askUser(
        "Clear Speedrun study history on this desktop?\n\n"
        "Removes practice attempts and resets matured sample cards. "
        "Imported decks stay. Then tap Sync now to re-test sync.",
        title="Speedrun",
    ):
        return

    def task() -> tuple[int, int]:
        attempt_cards = col.db.list("select distinct cid from sr_attempts")
        card_ids: set[int] = set(attempt_cards)
        try:
            content_did = col.decks.id(_CONTENT_DECK)
            if content_did:
                for cid in col.find_cards(f"deck:{content_did} prop:ivl>=1"):
                    card_ids.add(cid)
        except Exception:
            pass
        attempts = col.db.scalar("select count(*) from sr_attempts") or 0
        col.db.execute("delete from sr_attempts")
        col.db.execute("delete from sr_readiness")
        if card_ids:
            col.sched.schedule_cards_as_new(
                [CardId(cid) for cid in card_ids],
                restore_position=False,
                reset_counts=True,
            )
        col.set_config(_CFG_SAMPLE, False)
        refresh_readiness_after_sync(col)
        return attempts, len(card_ids)

    def finished(counts: tuple[int, int]) -> None:
        attempts, cards = counts
        _refresh(mw)
        tooltip(
            f"Cleared {attempts} attempts and reset {cards} cards. "
            "Tap Sync now to pull the phone's data."
        )
        if callable(on_done):
            on_done(counts)

    _run_with_progress(mw, "Clearing study data…", task, finished)


def _do_import_e2e(col) -> tuple[int, int] | None:
    """Create the curated biology deck + register its matched questions.

    Returns (cards_added, questions_added), (0, 0) if already present, or None if
    the pack isn't bundled. Idempotent; shared by the button and the first-run
    auto-load. Caller handles UI/refresh.
    """
    pack = _load_pack(_E2E_PACK)
    if pack is None:
        return None
    deck_name = pack.get("deck", "Speedrun Biology (e2e)")
    topic = pack.get("topic", "biology")
    if col.decks.by_name(deck_name) and col.find_cards(f'deck:"{deck_name}"'):
        return (0, 0)
    did = col.decks.id(deck_name)
    model = col.models.by_name("Basic")
    added = 0
    for card in pack.get("cards", []):
        note = col.new_note(model)
        note["Front"] = card.get("front", "")
        note["Back"] = card.get("back", "")
        note.tags = [topic]
        col.add_note(note, did)
        added += 1
    n_q = _import_question_pack(col, pack)
    return (added, n_q)


def import_e2e_pack(mw: aqt.AnkiQt) -> None:
    """One-tap curated end-to-end test: a small biology deck whose held-out
    questions are topic-matched by construction, so reviewing it and finishing
    pulls a relevant reasoning round (not random questions)."""
    col = mw.col
    if col is None:
        return
    if _load_pack(_E2E_PACK) is None:
        showWarning(
            "The biology e2e pack isn't bundled with this build. Rebuild, or "
            "import a .json pack via 'Import your own'."
        )
        return
    try:
        result = _do_import_e2e(col)
    except Exception as exc:  # pragma: no cover - surfaced to the user
        showWarning(f"Speedrun: e2e import failed: {exc}")
        return
    _refresh(mw)
    if result == (0, 0):
        tooltip(
            "The biology example deck is already imported \u2014 open it and review."
        )
    elif result:
        tooltip(
            f"Imported {result[0]} biology cards + {result[1]} matched questions. "
            "Review the deck and finish it to get the matched reasoning round."
        )


def maybe_load_example_deck(mw: aqt.AnkiQt) -> None:
    """First-run demo convenience: seed the bundled open-licensed MCAT content
    library once, on a fresh/empty collection (a tagged deck across all 31
    content categories plus matched practice questions, and the coverage map).
    Skipped if the user already has decks or cards. Falls back to the smaller
    biology example deck if the content library isn't bundled."""
    col = mw.col
    if col is None:
        return
    try:
        if col.get_config(_CFG_EXAMPLE, False):
            return
        user_decks = [d for d in col.decks.all_names_and_ids() if d.id != 1]
        if user_decks or col.card_count() > 0:
            col.set_config(_CFG_EXAMPLE, True)  # existing content: never auto-seed
            return
        if _do_import_content_library(col) is None:
            _do_import_e2e(col)
        col.set_config(_CFG_EXAMPLE, True)
        _refresh(mw)
    except Exception as exc:  # pragma: no cover - never block startup
        print(f"speedrun: example content auto-load skipped: {exc}")


def import_mmlu_pack(mw: aqt.AnkiQt) -> None:
    if mw.col is None:
        return
    pack = _load_pack(_MMLU_PACK)
    if pack is None:
        showWarning(
            "The MMLU pack isn't bundled with this build. Use "
            "tools/import_mmlu_pack.py to generate it, or import a .json pack."
        )
        return

    def task() -> int:
        return _import_question_pack(mw.col, pack)

    def on_done(n: int) -> None:
        _refresh(mw)
        tooltip(f"Added {n} MMLU practice questions to your held-out bank.")

    _run_with_progress(mw, "Importing MMLU practice pack…", task, on_done)


def import_cars_pack(mw: aqt.AnkiQt) -> None:
    """One-tap import of the bundled CARS starter pack: a handful of public-
    domain / openly-licensed passages with author-written passage questions
    (topic ``cars``), so CARS becomes a real, honest practice section."""
    if mw.col is None:
        return
    pack = _load_pack(_CARS_PACK)
    if pack is None:
        showWarning(
            "The CARS starter pack isn't bundled with this build. Import a .json "
            'pack with passage questions (topic "cars").'
        )
        return

    def task() -> int:
        return _import_question_pack(mw.col, pack)

    def on_done(n: int) -> None:
        _refresh(mw)
        tooltip(f"Added {n} CARS passage questions to your held-out bank.")

    _run_with_progress(mw, "Importing CARS starter pack…", task, on_done)


def _refresh(mw: aqt.AnkiQt) -> None:
    try:
        # If an in-place Speedrun screen owns the webview, repaint *it* rather
        # than refreshing the native overview/deck list beneath it, which would
        # otherwise flash the native surface over the Speedrun screen.
        from aqt import speedrun

        if speedrun.rerender_active_workspace(mw):
            mw.toolbar.redraw()
            return
        if mw.state == "overview":
            mw.overview.refresh()
        elif mw.state == "deckBrowser":
            mw.deckBrowser.refresh()
        else:
            mw.reset()
        mw.toolbar.redraw()
    except Exception:
        pass


# --- content status ---------------------------------------------------------


def _content_status(mw: aqt.AnkiQt) -> str:
    col = mw.col
    decks = 0
    cards = 0
    questions = 0
    try:
        decks = max(len(col.decks.all_names_and_ids()) - 1, 0)  # minus Default
        cards = col.card_count()
    except Exception:
        pass
    try:
        rep = col._backend.get_performance_report()
        questions = getattr(rep, "question_items", 0) or getattr(rep, "total_items", 0)
    except Exception:
        pass
    bits = [f"{decks} deck{'s' if decks != 1 else ''}", f"{cards} cards"]
    if questions:
        bits.append(f"{questions} practice questions")
    return " · ".join(bits)


# --- inline library (rendered into the main webview by speedrun.py) ---------
#
# The library is a Speedrun screen like Home/Practice/Settings: speedrun.py paints
# ``theme.library_body`` into mw.web and routes the ``speedrun:lib:*`` pycmds back
# to the action functions below. The catalog data + native file/link pickers live
# here; the pickers are the only OS dialogs, parented on the main window.


def content_status(mw: aqt.AnkiQt) -> str:
    """One-line summary of on-device content, for the library header."""
    return _content_status(mw)


def popular_decks() -> list[dict]:
    """The curated starter catalog rendered as cards on the library screen."""
    return _POPULAR_DECKS


def download_popular_deck(mw: aqt.AnkiQt, index: int) -> None:
    """Download + import the catalog deck at ``index`` (from the library card)."""
    if mw.col is None or not 0 <= index < len(_POPULAR_DECKS):
        return
    deck = _POPULAR_DECKS[index]
    download_and_import_deck(mw, deck["url"], deck["name"])


def pick_file(mw: aqt.AnkiQt) -> None:
    """Choose a local deck or question pack to import (native file picker)."""
    if mw.col is None:
        return
    path = getFile(
        mw,
        "Import a deck or question pack",
        None,
        key="speedrun_import",
        filter="Anki / Speedrun (*.apkg *.colpkg *.json *.csv)",
    )
    if isinstance(path, (list, tuple)):
        path = path[0] if path else None
    if path:
        import_local_file(mw, str(path))


def paste_link(mw: aqt.AnkiQt) -> None:
    """Import from a pasted direct link (Google Drive links resolve)."""
    if mw.col is None:
        return
    url, ok = QInputDialog.getText(
        mw,
        "Import from a link",
        "Direct .apkg/.colpkg/.json link (Drive links work):",
    )
    if ok and url.strip():
        name = url.strip().rsplit("/", 1)[-1][:40] or "deck"
        download_and_import_deck(mw, url.strip(), name)


# --- first-run onboarding ---------------------------------------------------

# Target tiers on the MCAT 472-528 scale (label -> target score).
_TIERS = [
    ("Competitive (515+)", 515),
    ("Strong (508)", 508),
    ("Solid (500)", 500),
    ("Building a base (492)", 492),
]


class ExamProfileDialog(QDialog):
    """Shared exam-profile editor used by BOTH first-run onboarding and 'Edit
    exam target', so the target you set is edited later with the same controls
    (an exam date + a target tier) - no more weeks-vs-date / tiers-vs-spinbox
    mismatch between the two entry points."""

    def __init__(
        self,
        mw: aqt.AnkiQt,
        *,
        heading: str,
        subtitle: str,
        ok_text: str,
        mark_onboarded: bool,
    ) -> None:
        super().__init__(mw)
        self.mw = mw
        self._mark_onboarded = mark_onboarded
        self.setWindowTitle("Speedrun")
        disable_help_button(self)
        self.resize(480, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(8)
        heading_label = _mark(QLabel(heading), role="display")
        layout.addWidget(heading_label)
        subtitle_label = _mark(QLabel(subtitle), role="muted")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)

        # Group each field with its eyebrow: a wider gap opens above each section
        # header, a tight gap sits between the header and its control, so the form
        # reads as two labelled groups rather than an evenly-spaced control stack.
        layout.addSpacing(10)
        eyebrow_date = _mark(QLabel("EXAM DATE"), role="eyebrow")
        layout.addWidget(eyebrow_date)
        layout.addSpacing(2)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate.currentDate().addDays(90))
        layout.addWidget(self.date)

        layout.addSpacing(12)
        eyebrow_target = _mark(QLabel("TARGET SCORE"), role="eyebrow")
        layout.addWidget(eyebrow_target)
        layout.addSpacing(2)
        self._target = 508
        self._tier_buttons: list[tuple[QPushButton, int]] = []
        for label, score in _TIERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            # A selectable option row (styled via QSS srRole="option" + :checked),
            # not a primary CTA - so the selected tier does not look like a second
            # "Start studying" button competing with the real CTA below.
            _mark(btn, role="option")
            qconnect(btn.clicked, lambda _=False, s=score: self._select_tier(s))
            layout.addWidget(btn)
            self._tier_buttons.append((btn, score))

        self._prefill()
        self._sync_tier_buttons()

        layout.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(ok_text)
        _mark(ok, primary=True)
        qconnect(buttons.accepted, self._save)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet(theme.dialog_qss(_night()))

    def _prefill(self) -> None:
        try:
            profile = self.mw.col._backend.get_exam_profile()
            if profile.exam_date_ms > 0:
                dt = datetime.fromtimestamp(
                    profile.exam_date_ms / 1000, tz=timezone.utc
                )
                self.date.setDate(QDate(dt.year, dt.month, dt.day))
            if profile.target_score > 0:
                self._target = profile.target_score
        except Exception:
            pass

    def _select_tier(self, score: int) -> None:
        self._target = score
        self._sync_tier_buttons()

    def _sync_tier_buttons(self) -> None:
        # The selected look is driven by the button's :checked pseudo-state (see
        # dialog_qss srRole="option"); repolish so the state repaints at once.
        for btn, score in self._tier_buttons:
            btn.setChecked(score == self._target)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _save(self) -> None:
        qd = self.date.date()
        dt = datetime(qd.year(), qd.month(), qd.day(), tzinfo=timezone.utc)
        exam_ms = int(dt.timestamp() * 1000)
        try:
            self.mw.col._backend.set_exam_profile(
                speedrun_pb2.ExamProfile(
                    exam_date_ms=exam_ms, target_score=self._target
                )
            )
            if self._mark_onboarded:
                self.mw.col.set_config(_CFG_ONBOARDED, True)
            _refresh(self.mw)
            tooltip(
                "Target saved. Your Speedrun plan is set."
                if self._mark_onboarded
                else "Exam target saved."
            )
        except Exception as exc:
            showWarning(f"Speedrun: could not save your target: {exc}")
        self.accept()


_ONBOARD_SUBTITLE = (
    "Speedrun anchors your plan to a date and a target score, then keeps three "
    "honest signals: memory, performance, and readiness."
)


def _exam_dialog(mw: aqt.AnkiQt, *, onboarding: bool) -> ExamProfileDialog:
    if onboarding:
        return ExamProfileDialog(
            mw,
            heading="Let's set your target",
            subtitle=_ONBOARD_SUBTITLE,
            ok_text="Start studying",
            mark_onboarded=True,
        )
    return ExamProfileDialog(
        mw,
        heading="Exam target",
        subtitle="Anchor your plan to a date and a target score.",
        ok_text="Save",
        mark_onboarded=False,
    )


def open_onboarding(mw: aqt.AnkiQt) -> None:
    if mw.col is None:
        return
    _exam_dialog(mw, onboarding=True).exec()


def open_exam_target(mw: aqt.AnkiQt) -> None:
    """Edit the exam profile with the same dialog onboarding uses."""
    if mw.col is None:
        return
    _exam_dialog(mw, onboarding=False).exec()


def maybe_show_onboarding(mw: aqt.AnkiQt) -> None:
    """Show onboarding once, on first launch with no exam profile set."""
    col = mw.col
    if col is None:
        return
    try:
        if col.get_config(_CFG_ONBOARDED, False):
            return
        profile = col._backend.get_exam_profile()
        if getattr(profile, "exam_date_ms", 0) > 0:
            col.set_config(_CFG_ONBOARDED, True)
            return
    except Exception:
        return
    _exam_dialog(mw, onboarding=True).exec()
