#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun §7c coverage map (AI-off).

Lists every topic on the AAMC MCAT content outline, marks which ones a deck
covers, reports percent covered (plain and weight-weighted), and demonstrates
that readiness *abstains* when coverage is below the give-up line
(MIN_COVERAGE = 0.50).

The engine ships only a 10-item placeholder outline (the 10 Foundational
Concepts). This tool supplies the FULL, finer AAMC content outline as data
(tools/speedrun_mcat_outline.json: 31 discipline "content categories", ids
1A..10A) and loads it into the engine at runtime via SetTopicMap - no Rust
change. Everything goes through the SpeedrunService backend (the same engine
the desktop app and the phone use); no AI is involved.

Usage:
    python tools/speedrun_coverage_map.py                 # synthetic demo + self-test
    python tools/speedrun_coverage_map.py collection.anki2  # report a real deck

With no argument it builds synthetic collections, prints the coverage map, and
asserts a set of invariants (a re-runnable self-test). With a collection path it
loads the outline onto a *copy* of that collection (non-destructive) and reports
the deck's coverage against the full outline.

Run it via the wrapper so the built pylib + bridge are on the path:
    ./tools/speedrun_coverage_map.sh
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

from anki import speedrun_pb2
from anki.collection import Collection

# Mirrors rslib MIN_COVERAGE (rslib/src/speedrun/readiness.rs): readiness
# abstains (sufficient=false) while *unweighted* topic coverage < 0.50. Weighted
# coverage is reported alongside as the sharper signal for skipped high-weight
# sections (see the weighted-skip demo below).
MIN_COVERAGE = 0.5

# Mirrors rslib MIN_CARDS_PER_TOPIC (rslib/src/speedrun/coverage.rs): a topic
# only counts as covered once it holds at least this many tagged cards, so a
# lone incidental card can no longer light up a whole topic. Covered topics in
# this demo are seeded with exactly this many cards, so the pure-Python
# expected-coverage cross-check (which treats "covered" as membership) still
# matches the engine's card-count bar.
MIN_CARDS_PER_TOPIC = 3

# Give-up thresholds from rslib/src/speedrun/readiness.rs, used only to seed the
# memory/performance dimensions so the *coverage* dimension is the one on show.
MIN_REVIEW_CARDS = 20
MIN_EXAM_ATTEMPTS = 20
MIN_GRADED_ATTEMPTS = 30

SECTION_ORDER = ["Bio/Biochem", "Chem/Phys", "Psych/Soc"]

OUTLINE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "speedrun_mcat_outline.json"
)


class Checker:
    """Tiny assertion helper that logs each step like a workflow narrative."""

    def __init__(self) -> None:
        self.n = 0

    def ok(self, label: str, cond: bool, detail: str = "") -> None:
        self.n += 1
        if not cond:
            raise AssertionError(f"FAIL: {label}" + (f" ({detail})" if detail else ""))
        print(f"  \u2713 {label}" + (f"  \u2014 {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
# Outline loading + pure-Python coverage math (independent of the engine, so
# the self-test can cross-check the engine's numbers).
# --------------------------------------------------------------------------- #
def load_outline(path: str = OUTLINE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        outline = json.load(f)
    topics = outline["topics"]
    ids = [t["id"] for t in topics]
    if len(set(ids)) != len(ids):
        raise ValueError("outline has duplicate topic ids")
    return outline


def to_entries(outline: dict) -> list[speedrun_pb2.TopicMapEntry]:
    """Build the TopicMap entries the backend expects (topic=id, label=name)."""
    return [
        speedrun_pb2.TopicMapEntry(
            topic=t["id"], label=t["name"], weight=float(t["weight"])
        )
        for t in outline["topics"]
    ]


def expected_coverage(topics: list[dict], covered_ids) -> dict:
    """Compute coverage the way the engine does, but in plain Python."""
    covered = set(covered_ids)
    total = len(topics)
    n_cov = sum(1 for t in topics if t["id"] in covered)
    w_sum = sum(t["weight"] for t in topics)
    w_cov = sum(t["weight"] for t in topics if t["id"] in covered)
    return {
        "topics_total": total,
        "topics_covered": n_cov,
        "coverage": (n_cov / total) if total else 0.0,
        "weighted_coverage": (w_cov / w_sum) if w_sum else 0.0,
    }


def ids_for_sections(outline: dict, sections) -> list[str]:
    sset = set(sections)
    return [t["id"] for t in outline["topics"] if t["section"] in sset]


def ids_for_concepts(outline: dict, concepts) -> list[str]:
    cset = set(concepts)
    return [t["id"] for t in outline["topics"] if t["concept"] in cset]


# --------------------------------------------------------------------------- #
# Collection helpers.
# --------------------------------------------------------------------------- #
def new_collection() -> Collection:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path)


def load_outline_into(col: Collection, outline: dict) -> int:
    """Replace the engine's topic map with our full outline via SetTopicMap."""
    return col._backend.set_topic_map(to_entries(outline))


def add_tagged_cards(
    col: Collection, tag_counts: dict[str, int], mature: bool = True
) -> int:
    """Add `count` notes tagged with each topic id. If mature, mark every card a
    mature review card so it counts toward the memory substrate."""
    model = col.models.by_name("Basic")
    did = col.decks.id("Default")
    made = 0
    for tag, count in tag_counts.items():
        for i in range(count):
            note = col.new_note(model)
            note["Front"] = f"{tag} study card {i}"
            note["Back"] = "answer"
            note.tags = [tag]
            col.add_note(note, did)
            made += 1
    if mature:
        # Mature review card = type 2 (review), interval >= 21 days. This is the
        # memory substrate readiness counts (rslib sr_card_counts).
        col.db.execute("update cards set type = 2, queue = 2, ivl = 30")
    return made


def record_exam_attempts(col: Collection, n: int, correct_frac: float = 0.6) -> None:
    """Record n exam-style (question_type != 0) attempts so the performance and
    graded-attempt give-up gates pass. Card/note ids are arbitrary here - the
    performance gate only counts rows."""
    n_correct = int(round(n * correct_frac))
    for i in range(n):
        correct = i < n_correct
        col._backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=1,
                note_id=1,
                session_id="coverage-map",
                answered_at_ms=1_700_000_000_000 + i,
                took_ms=8000,
                question_type=1,
                correct=correct,
                predicted=0.6,
                signals=speedrun_pb2.ClassifyAttemptRequest(
                    correct=correct, took_ms=8000, question_type=1
                ),
                data="{}",
            )
        )


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #
def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def print_coverage_table(cov, outline: dict) -> None:
    """Pretty-print the engine's CoverageReport grouped by MCAT section."""
    meta = {t["id"]: t for t in outline["topics"]}
    rows = {t.topic: t for t in cov.topics}

    print(f"  {'id':<4} {'wt':>4} {'cards':>6}  {'status':<9} name")
    print(f"  {'-' * 4} {'-' * 4} {'-' * 6}  {'-' * 9} {'-' * 40}")
    for section in SECTION_ORDER:
        sec_ids = [t["id"] for t in outline["topics"] if t["section"] == section]
        n_cov = sum(1 for tid in sec_ids if rows[tid].covered)
        w_sum = sum(meta[tid]["weight"] for tid in sec_ids)
        w_cov = sum(meta[tid]["weight"] for tid in sec_ids if rows[tid].covered)
        full = outline["sections"].get(section, section)
        print(
            f"  [{section}] {full}"
            f"  -  {n_cov}/{len(sec_ids)} topics, weighted {pct(w_cov / w_sum)}"
        )
        for tid in sec_ids:
            r = rows[tid]
            status = "COVERED" if r.covered else "gap"
            print(
                f"  {tid:<4} {r.weight:>4.1f} {r.cards:>6}  {status:<9} {meta[tid]['name']}"
            )
    print()
    print(f"  topics_total       : {cov.topics_total}")
    print(f"  topics_covered     : {cov.topics_covered}")
    print(f"  coverage (plain)   : {pct(cov.coverage)}")
    print(f"  weighted_coverage  : {pct(cov.weighted_coverage)}")
    covered = [t.topic for t in cov.topics if t.covered]
    gaps = [t.topic for t in cov.topics if not t.covered]
    print(
        f"  covered  ({len(covered):>2}) : {', '.join(covered) if covered else '(none)'}"
    )
    print(f"  gaps     ({len(gaps):>2}) : {', '.join(gaps) if gaps else '(none)'}")


def assert_engine_matches_expected(
    check: Checker, cov, outline: dict, covered_ids, label: str
) -> None:
    """Cross-check the engine's CoverageReport against pure-Python expectations."""
    exp = expected_coverage(outline["topics"], covered_ids)
    check.ok(
        f"{label}: engine topic count matches the custom outline (not the 10-item placeholder)",
        cov.topics_total == exp["topics_total"] == len(outline["topics"]),
        f"{cov.topics_total}",
    )
    check.ok(
        f"{label}: topics_covered matches",
        cov.topics_covered == exp["topics_covered"],
        f"{cov.topics_covered} == {exp['topics_covered']}",
    )
    check.ok(
        f"{label}: unweighted coverage matches",
        abs(cov.coverage - exp["coverage"]) < 1e-4,
        f"{cov.coverage:.4f} == {exp['coverage']:.4f}",
    )
    check.ok(
        f"{label}: weighted coverage matches",
        abs(cov.weighted_coverage - exp["weighted_coverage"]) < 1e-4,
        f"{cov.weighted_coverage:.4f} == {exp['weighted_coverage']:.4f}",
    )


# --------------------------------------------------------------------------- #
# Synthetic demo + self-test.
# --------------------------------------------------------------------------- #
def demo(outline: dict) -> int:
    check = Checker()
    topics = outline["topics"]
    n_topics = len(topics)

    print(f"\nLoaded outline: {outline['name']}")
    print(f"  source : {outline['source']}")
    print(
        f"  topics : {n_topics} content categories (vs the engine's 10-concept placeholder)"
    )
    total_w = sum(t["weight"] for t in topics)
    for section in SECTION_ORDER:
        ids = [t for t in topics if t["section"] == section]
        w = sum(t["weight"] for t in ids)
        print(
            f"    {section:<12} {len(ids):>2} topics, weight {w:>5.1f} ({pct(w / total_w)} of exam weight)"
        )

    # ------------------------------------------------------------------ #
    print("\n[1] Coverage map for a partially-studied deck")
    covered1 = [
        "1A",
        "1B",
        "1D",
        "2A",
        "2C",
        "3A",
        "3B",
        "4A",
        "4B",
        "5A",
        "5D",
        "6A",
        "6B",
        "7A",
        "8A",
        "8B",
        "9A",
        "10A",
    ]
    col = new_collection()
    try:
        loaded = load_outline_into(col, outline)
        check.ok(
            "SetTopicMap loaded the full custom outline",
            loaded == n_topics,
            f"{loaded} topics",
        )
        check.ok(
            "GetTopicMap reads the custom ids back (1A..10A, not fc1..fc10)",
            {e.topic for e in col._backend.get_topic_map()}
            == {t["id"] for t in topics},
        )
        add_tagged_cards(col, {tid: MIN_CARDS_PER_TOPIC for tid in covered1})
        cov = col._backend.get_coverage_report()
        print_coverage_table(cov, outline)
        assert_engine_matches_expected(check, cov, outline, covered1, "map")
    finally:
        col.close()

    # ------------------------------------------------------------------ #
    print("\n[2] Abstain below the line: a high-volume deck that skips the sciences")
    print("    (the spec case: 'a deck with 10,000 cards that skips a whole")
    print("     high-weight section should not show ready')")
    psych_ids = ids_for_sections(outline, ["Psych/Soc"])  # the low-weight section
    science_ids = ids_for_sections(outline, ["Bio/Biochem", "Chem/Phys"])  # skipped
    volume = 10_000
    col = new_collection()
    try:
        load_outline_into(col, outline)
        # 10,000 cards, but every one of them lands in the low-weight Psych/Soc
        # section - so the deck is huge yet structurally thin.
        per = volume // len(psych_ids)
        tag_counts = {tid: per for tid in psych_ids}
        # top up so the total is exactly `volume`
        tag_counts[psych_ids[0]] += volume - per * len(psych_ids)
        t0 = time.time()
        made = add_tagged_cards(col, tag_counts)
        record_exam_attempts(col, MIN_GRADED_ATTEMPTS)  # clear memory+performance gates
        dt = time.time() - t0
        review_cards = col.db.scalar("select count(*) from cards where type = 2")
        print(
            f"    built {made} cards across {len(psych_ids)} Psych/Soc topics in {dt:.1f}s"
            f" ({review_cards} mature review cards)"
        )

        cov = col._backend.get_coverage_report()
        snap = col._backend.compute_readiness()
        print(
            f"    topics covered     : {cov.topics_covered}/{cov.topics_total}"
            f"  ({', '.join(science_ids[:3])}... all skipped)"
        )
        print(
            f"    coverage (plain)   : {pct(cov.coverage)}   (< {pct(MIN_COVERAGE)} line)"
        )
        print(
            f"    weighted_coverage  : {pct(cov.weighted_coverage)}   (heavier skip shows up here)"
        )
        print(f"    readiness.sufficient : {snap.sufficient}")
        print(f"    readiness.blocking   : {snap.blocking_dimension}")
        print(f"    readiness.reason     : {snap.reason}")

        assert_engine_matches_expected(check, cov, outline, psych_ids, "abstain")
        check.ok(
            "plain coverage is below the line",
            cov.coverage < MIN_COVERAGE,
            pct(cov.coverage),
        )
        check.ok(
            "10,000 cards do NOT buy readiness - it abstains",
            not snap.sufficient,
        )
        check.ok(
            "coverage is the blocking dimension (memory+performance were satisfied)",
            snap.blocking_dimension == "coverage",
            snap.blocking_dimension,
        )
        check.ok("the reason names topic coverage", "topic coverage" in snap.reason)

        # -------------------------------------------------------------- #
        print("\n[3] Cross the line: add the missing high-weight sciences until")
        print("    BOTH plain and weighted coverage cross 50%")
        # The engine gates on min(plain, weighted): adding a few low-weight topics
        # (or only one science concept) is not enough. Cover the high-weight
        # Bio/Biochem + Chem/Phys sciences so BOTH metrics clear the line.
        add_ids = science_ids
        add_tagged_cards(col, {tid: MIN_CARDS_PER_TOPIC for tid in add_ids})
        now_covered = psych_ids + add_ids
        cov2 = col._backend.get_coverage_report()
        snap2 = col._backend.compute_readiness()
        print(
            f"    added coverage of  : the {len(add_ids)} Bio/Biochem + Chem/Phys science topics"
        )
        print(
            f"    coverage (plain)   : {pct(cov2.coverage)}   (>= {pct(MIN_COVERAGE)} line)"
        )
        print(
            f"    weighted_coverage  : {pct(cov2.weighted_coverage)}   (>= {pct(MIN_COVERAGE)} line)"
        )
        print(
            f"    effective coverage : min(plain, weighted) = {pct(min(cov2.coverage, cov2.weighted_coverage))}"
        )
        print(f"    readiness.sufficient : {snap2.sufficient}")
        print(f"    readiness.blocking   : {snap2.blocking_dimension}")
        print(f"    readiness.reason     : {snap2.reason}")

        assert_engine_matches_expected(check, cov2, outline, now_covered, "crossed")
        check.ok(
            "plain coverage is now at/above the line",
            cov2.coverage >= MIN_COVERAGE,
            pct(cov2.coverage),
        )
        check.ok(
            "weighted coverage is now at/above the line too",
            cov2.weighted_coverage >= MIN_COVERAGE,
            pct(cov2.weighted_coverage),
        )
        check.ok(
            "both metrics cross, so readiness stops abstaining on the coverage dimension",
            snap2.blocking_dimension != "coverage"
            and "topic coverage" not in snap2.reason,
            f"blocking={snap2.blocking_dimension}",
        )
        check.ok(
            "with every gate satisfied, readiness is now sufficient",
            snap2.sufficient,
            snap2.reason,
        )
    finally:
        col.close()

    # ------------------------------------------------------------------ #
    print("\n[4] The engine abstains on a skipped high-weight section")
    print("    A deck can touch a MAJORITY of topics yet still be short on exam")
    print("    weight if it skips the heavy sections. Here: cover all Psych/Soc")
    print("    + all non-biochem Biology, but skip biomolecules (FC1) and the")
    print("    entire Chem/Phys section. The give-up rule now gates on")
    print("    min(plain, weighted), so this deck must NOT show ready.")
    covered4 = ids_for_sections(outline, ["Psych/Soc"]) + ids_for_concepts(
        outline, ["FC2", "FC3"]
    )
    col = new_collection()
    try:
        load_outline_into(col, outline)
        # Seed memory + performance the same way section [2] does, so coverage is
        # the binding dimension: MIN_CARDS_PER_TOPIC mature review cards per
        # covered topic clears both the coverage bar and MIN_REVIEW_CARDS, and the
        # exam attempts clear the performance + graded gates.
        add_tagged_cards(col, {tid: MIN_CARDS_PER_TOPIC for tid in covered4})
        record_exam_attempts(col, MIN_GRADED_ATTEMPTS)
        review_cards = col.db.scalar("select count(*) from cards where type = 2")
        cov = col._backend.get_coverage_report()
        snap = col._backend.compute_readiness()
        skipped = [t["id"] for t in topics if t["id"] not in set(covered4)]
        effective = min(cov.coverage, cov.weighted_coverage)
        print(
            f"    seeded             : {review_cards} mature review cards (>= {MIN_REVIEW_CARDS}),"
            f" {MIN_GRADED_ATTEMPTS} exam attempts"
        )
        print(f"    topics covered     : {cov.topics_covered}/{cov.topics_total}")
        print(
            f"    coverage (plain)   : {pct(cov.coverage)}   (looks above the {pct(MIN_COVERAGE)} line)"
        )
        print(
            f"    weighted_coverage  : {pct(cov.weighted_coverage)}   (below it - the heavy skip shows)"
        )
        print(
            f"    effective coverage : min(plain, weighted) = {pct(effective)}   <- engine gates here"
        )
        print(f"    skipped (heavy)    : {', '.join(skipped)}")
        print(f"    readiness.sufficient : {snap.sufficient}")
        print(f"    readiness.blocking   : {snap.blocking_dimension}")
        print(f"    readiness.reason     : {snap.reason}")

        assert_engine_matches_expected(check, cov, outline, covered4, "weighted-skip")
        check.ok(
            "weighted coverage drops below plain coverage when a heavy section is skipped",
            cov.weighted_coverage < cov.coverage,
            f"{pct(cov.weighted_coverage)} < {pct(cov.coverage)}",
        )
        check.ok(
            "plain coverage alone would clear the line, but weighted does not",
            cov.coverage >= MIN_COVERAGE > cov.weighted_coverage,
            f"plain {pct(cov.coverage)} >= {pct(MIN_COVERAGE)} > weighted {pct(cov.weighted_coverage)}",
        )
        check.ok(
            "the engine abstains on the skipped high-weight section",
            not snap.sufficient,
        )
        check.ok(
            "coverage is the blocking dimension (memory + performance were seeded)",
            snap.blocking_dimension == "coverage",
            snap.blocking_dimension,
        )
        check.ok(
            "the abstain reason reports the weighted figure",
            "weighted" in snap.reason,
            snap.reason,
        )
    finally:
        col.close()

    print(f"\nself-test: PASS ({check.n} checks)")
    return 0


# --------------------------------------------------------------------------- #
# Real-collection mode (non-destructive: works on a temp copy).
# --------------------------------------------------------------------------- #
def report_real_collection(path: str, outline: dict) -> int:
    if not os.path.exists(path):
        print(f"collection not found: {path}", file=sys.stderr)
        return 1
    # Work on a copy so we never mutate the user's real topic map / snapshots.
    tmpdir = tempfile.mkdtemp(prefix="speedrun_cov_")
    copy_path = os.path.join(tmpdir, "collection.anki2")
    shutil.copy(path, copy_path)
    col = Collection(copy_path)
    try:
        loaded = load_outline_into(col, outline)
        print(f"\nLoaded {loaded}-topic AAMC outline onto a copy of {path}")
        print("(non-destructive: your real collection is untouched)\n")
        cov = col._backend.get_coverage_report()
        print_coverage_table(cov, outline)
        snap = col._backend.compute_readiness()
        print()
        print(f"  readiness.sufficient : {snap.sufficient}")
        print(f"  readiness.blocking   : {snap.blocking_dimension}")
        print(f"  readiness.reason     : {snap.reason}")
        if cov.topics_covered == 0:
            print(
                "\n  note: 0 topics covered means this deck's notes are not tagged with the\n"
                f"  outline ids (1A..10A), or each tagged topic has fewer than {MIN_CARDS_PER_TOPIC}\n"
                f"  cards. Coverage requires at least {MIN_CARDS_PER_TOPIC} notes tagged with a\n"
                "  topic id; tag more notes with the content-category ids to populate the map."
            )
    finally:
        col.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


def main() -> int:
    outline = load_outline()
    if len(sys.argv) > 1:
        return report_real_collection(sys.argv[1], outline)
    return demo(outline)


if __name__ == "__main__":
    raise SystemExit(main())
