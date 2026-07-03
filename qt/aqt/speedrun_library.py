# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun content library + first-run onboarding for the desktop app.

Brings the mobile Library and onboarding flows to the desktop so the two apps
match:

- a curated popular-deck catalog with one-tap download + import (Google Drive
  share links are resolved past the virus-scan interstitial),
- one-tap import of the bundled, open-licensed MMLU practice pack,
- import-your-own (a file picker, or a pasted direct link),
- a first-run onboarding dialog (weeks-to-exam + target score) that seeds the
  exam profile via ``SetExamProfile``.

Everything runs through the existing ImportExport / SpeedrunService boundary.
There is no AI here; network access happens only when the user taps a button.
"""

from __future__ import annotations

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
from aqt import speedrun_theme as theme
from aqt.qt import (
    QDate,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
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
_E2E_PACK = "speedrun_e2e_biology.json"
_PAYLOAD_KEYS = ("stem", "options", "correct_index", "explanation")

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
        return _import_package_file(mw, path)

    def on_done(msg: str) -> None:
        _refresh(mw)
        tooltip(msg)

    _run_with_progress(mw, "Importing…", task, on_done)


_CFG_EXAMPLE = "speedrunExampleLoaded"


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
    """First-run demo convenience: seed the bundled biology example deck once, on
    a fresh/empty collection. Skipped if the user already has decks or cards."""
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
        _do_import_e2e(col)
        col.set_config(_CFG_EXAMPLE, True)
        _refresh(mw)
    except Exception as exc:  # pragma: no cover - never block startup
        print(f"speedrun: example deck auto-load skipped: {exc}")


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


def _refresh(mw: aqt.AnkiQt) -> None:
    try:
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


# --- library dialog ---------------------------------------------------------


class LibraryDialog(QDialog):
    """Desktop equivalent of the mobile Library: popular decks, the MMLU pack,
    and import-your-own, all one tap."""

    def __init__(self, mw: aqt.AnkiQt) -> None:
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("Speedrun library")
        disable_help_button(self)
        self.resize(560, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(12)

        layout.addWidget(_mark(QLabel("Library"), role="display"))
        layout.addWidget(
            _mark(
                QLabel(f"On this device: {_content_status(mw)}"),
                role="muted",
            )
        )

        layout.addWidget(_mark(QLabel("GUIDED END-TO-END TEST"), role="eyebrow"))
        e2e = QFrame()
        e2e.setProperty("srCard", "1")
        e2e_row = QHBoxLayout(e2e)
        e2e_row.setContentsMargins(14, 12, 14, 12)
        e2e_meta = QVBoxLayout()
        e2e_meta.addWidget(_mark(QLabel("Biology e2e test"), role="title"))
        e2e_meta.addWidget(
            _mark(
                QLabel(
                    "15 biology cards + 6 topic-matched questions. Review, finish, and the reasoning round pulls matched (not random) questions."
                ),
                role="muted",
            )
        )
        e2e_meta.itemAt(1).widget().setWordWrap(True)  # type: ignore[attr-defined]
        e2e_row.addLayout(e2e_meta, 1)
        add_e2e = _mark(QPushButton("Add e2e test"), primary=True)
        qconnect(add_e2e.clicked, lambda: import_e2e_pack(self.mw))
        e2e_row.addWidget(add_e2e)
        layout.addWidget(e2e)

        layout.addWidget(_divider())

        layout.addWidget(_mark(QLabel("POPULAR DECKS"), role="eyebrow"))
        for deck in _POPULAR_DECKS:
            layout.addWidget(self._deck_card(deck))

        layout.addWidget(_divider())

        layout.addWidget(_mark(QLabel("PRACTICE QUESTIONS"), role="eyebrow"))
        mmlu_row = QHBoxLayout()
        mmlu_meta = QVBoxLayout()
        mmlu_meta.addWidget(_mark(QLabel("MMLU MCAT-relevant pack"), role="title"))
        mmlu_meta.addWidget(
            _mark(QLabel("Open-licensed held-out questions (MIT)."), role="muted")
        )
        mmlu_row.addLayout(mmlu_meta, 1)
        add_mmlu = _mark(QPushButton("Add pack"), primary=True)
        qconnect(add_mmlu.clicked, lambda: import_mmlu_pack(self.mw))
        mmlu_row.addWidget(add_mmlu)
        layout.addLayout(mmlu_row)

        layout.addWidget(_divider())

        layout.addWidget(_mark(QLabel("IMPORT YOUR OWN"), role="eyebrow"))
        own_row = QHBoxLayout()
        pick = QPushButton("Choose file…")
        qconnect(pick.clicked, self._pick_file)
        paste = QPushButton("Paste a link…")
        qconnect(paste.clicked, self._paste_link)
        own_row.addWidget(pick)
        own_row.addWidget(paste)
        own_row.addStretch(1)
        layout.addLayout(own_row)
        layout.addWidget(
            _mark(
                QLabel(
                    ".apkg / .colpkg decks or .json question packs. Drive links work."
                ),
                role="muted",
            )
        )

        layout.addStretch(1)
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(close_box.rejected, self.reject)
        layout.addWidget(close_box)

        self.setStyleSheet(theme.dialog_qss(_night()))

    def _deck_card(self, deck: dict) -> QWidget:
        card = QFrame()
        card.setProperty("srCard", "1")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        meta = QVBoxLayout()
        meta.addWidget(_mark(QLabel(deck["name"]), role="title"))
        meta.addWidget(_mark(QLabel(deck["section"]), role="muted"))
        meta.addWidget(_mark(QLabel(deck["size"]), role="muted"))
        row.addLayout(meta, 1)
        btn = _mark(QPushButton("Download & import"), primary=True)
        qconnect(
            btn.clicked,
            lambda _=False, d=deck: download_and_import_deck(
                self.mw, d["url"], d["name"]
            ),
        )
        row.addWidget(btn)
        return card

    def _pick_file(self) -> None:
        path = getFile(
            self,
            "Import a deck or question pack",
            None,
            key="speedrun_import",
            filter="Anki / Speedrun (*.apkg *.colpkg *.json)",
        )
        if isinstance(path, (list, tuple)):
            path = path[0] if path else None
        if path:
            import_local_file(self.mw, str(path))

    def _paste_link(self) -> None:
        url, ok = QInputDialog.getText(
            self,
            "Import from a link",
            "Direct .apkg/.colpkg/.json link (Drive links work):",
        )
        if ok and url.strip():
            name = url.strip().rsplit("/", 1)[-1][:40] or "deck"
            download_and_import_deck(self.mw, url.strip(), name)


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setProperty("srRole", "divider")
    return line


def open_library(mw: aqt.AnkiQt) -> None:
    if mw.col is None:
        return
    LibraryDialog(mw).exec()


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
        layout.setSpacing(12)
        layout.addWidget(_mark(QLabel(heading), role="display"))
        layout.addWidget(_mark(QLabel(subtitle), role="muted"))

        layout.addWidget(_mark(QLabel("EXAM DATE"), role="eyebrow"))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate.currentDate().addDays(90))
        layout.addWidget(self.date)

        layout.addWidget(_mark(QLabel("TARGET SCORE"), role="eyebrow"))
        self._target = 508
        self._tier_buttons: list[tuple[QPushButton, int]] = []
        for label, score in _TIERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
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
        for btn, score in self._tier_buttons:
            btn.setChecked(score == self._target)
            _mark(btn, primary=(score == self._target))
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
