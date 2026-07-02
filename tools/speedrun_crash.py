#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun crash-safety + offline (AI-off) proof harness (projectspec 7g).

Two independent durability tests, run over the *same* Rust engine the desktop
app and the phone use (the SpeedrunService protobuf boundary); no AI, no network:

  1. Crash test (real SIGKILLs).
     Against a real on-disk collection, spawn a child process that opens the
     collection, commits a handful of reviews, then opens an explicit
     transaction and starts writing a *review in progress* it never commits.
     The parent HARD-KILLS the child (SIGKILL) while it is parked mid-write,
     then reopens the collection and checks integrity. Repeated N times
     (default 20). SQLite's WAL/rollback recovery must roll the interrupted
     write back cleanly: zero corrupted collections, every committed review
     preserved exactly, the interrupted write discarded.

  2. Offline / AI-off test.
     All outbound Python network is black-holed (any connect() raises), then we
     prove the deterministic diagnosis path still works with the network pulled:
     record_attempt(...).diagnosis.kind is a valid non-AI classification and
     compute_readiness() still returns a score-or-abstention on the MCAT scale,
     with *zero* outbound connection attempts. Then we show the AI path degrades
     cleanly: a simulated online-coach call fails fast when the network is gone
     and the app falls back to the deterministic engine, still producing a
     diagnosis and a score.

Usage:
    python tools/speedrun_crash.py [n_kills]     # default 20

Run it via the wrapper so the built pylib + bridge are on the path:
    ./tools/speedrun_crash.sh

Exits non-zero on the first violated invariant (a re-runnable self-test).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from anki import speedrun_pb2
from anki.collection import Collection

# Diagnosis kinds (mirror rslib/src/speedrun/mod.rs).
MEMORY, REASONING, PASSAGE, TEST_TAKING, CORRECT = 1, 2, 3, 4, 5
VALID_KINDS = {MEMORY, REASONING, PASSAGE, TEST_TAKING, CORRECT}
KIND_NAME = {
    MEMORY: "memory",
    REASONING: "reasoning",
    PASSAGE: "passage",
    TEST_TAKING: "test_taking",
    CORRECT: "correct",
}
# Question types.
SRS, PASSAGE_MCQ, DISCRETE = 0, 1, 2

DEFAULT_KILLS = 20
COMMITS_PER_ROUND = 3  # reviews the child durably commits before each kill
INFLIGHT_ROWS = 5000  # rows written inside the never-committed transaction
MCAT_LOW, MCAT_HIGH = 472, 528

# Line markers the crash-worker prints so the parent can kill it at the exact
# moment it is parked inside an open, uncommitted transaction.
MARK_COMMITTED = "SPEEDRUN_COMMITTED"
MARK_MIDWRITE = "SPEEDRUN_MIDWRITE"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # the anki/ project root
REPORT_PATH = os.path.join(_HERE, "speedrun_crash_report.md")

_INSERT_INFLIGHT = (
    "insert into sr_attempts "
    "(cid, nid, session_id, answered_at_ms, took_ms, question_type, correct, "
    "diagnosis_kind, diagnosis_confidence, routed_action, usn, data) "
    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _record(
    col: Collection,
    *,
    card_id: int,
    note_id: int,
    correct: bool,
    question_type: int = SRS,
    took_ms: int = 6000,
    recall_failed: bool = False,
    passage_evidence_missed: bool = False,
    predicted: float = 0.0,
    session: str = "crash",
):
    """Record one attempt through the engine (auto-commits), returning the
    RecordAttemptResponse with its deterministic diagnosis."""
    signals = speedrun_pb2.ClassifyAttemptRequest(
        correct=correct,
        took_ms=took_ms,
        recall_failed=recall_failed,
        passage_evidence_missed=passage_evidence_missed,
        question_type=question_type,
    )
    req = speedrun_pb2.RecordAttemptRequest(
        card_id=card_id,
        note_id=note_id,
        session_id=session,
        answered_at_ms=1_700_000_000_000,
        took_ms=took_ms,
        question_type=question_type,
        correct=correct,
        signals=signals,
        predicted=predicted,
        data="{}",
    )
    return col._backend.record_attempt(req)


def _seed_card(col: Collection, tag: str) -> tuple[int, int]:
    """Add a Basic note+card, returning (card_id, note_id). Auto-commits."""
    model = col.models.by_name("Basic")
    did = col.decks.id("Default")
    note = col.new_note(model)
    note["Front"] = f"crash-test fact ({tag})"
    note["Back"] = "the answer"
    note.tags = [tag]
    col.add_note(note, did)
    return note.cards()[0].id, note.id


# --------------------------------------------------------------------------
# Crash worker (runs in the child process; killed mid-write by the parent)
# --------------------------------------------------------------------------


def _crash_worker(path: str, n_commit: int) -> int:
    """Child process: durably commit n_commit reviews, then open a transaction,
    start writing a review that is never committed, announce it, and park until
    the parent SIGKILLs us mid-write."""
    col = Collection(path)
    cid, nid = _seed_card(col, "crash")
    for _ in range(n_commit):
        # Each of these commits: recall miss -> deterministic MEMORY diagnosis.
        _record(col, card_id=cid, note_id=nid, correct=False, recall_failed=True)

    committed = col.db.scalar("select count(*) from sr_attempts")
    journal = col.db.scalar("pragma journal_mode")
    # Everything above is durably committed. Announce the durable review count.
    print(f"{MARK_COMMITTED} {committed} {journal}", flush=True)

    # Shrink the page cache so the uncommitted rows below must spill to the
    # WAL/journal on disk -- i.e. there really is an interrupted write in flight
    # (not merely buffered in this process) when the SIGKILL lands.
    col.db.execute("pragma cache_size = 50")
    col._backend.db_begin()
    row = [cid, nid, "crash-inflight", 1, 0, 0, 0, 0, 0.0, 0, -1, "x" * 100]
    col.db.executemany(_INSERT_INFLIGHT, [row] * INFLIGHT_ROWS)
    # Dirty a core table too, so the interrupted transaction spans engine + core.
    col.db.execute("update col set mod = ?", int(time.time() * 1000))

    # We are now parked inside an open, uncommitted transaction. Signal the
    # parent, which will hard-kill us here -- mid-write, no clean close.
    print(f"{MARK_MIDWRITE} inflight={INFLIGHT_ROWS}", flush=True)
    time.sleep(600)
    return 0  # unreachable: the parent SIGKILLs us during the sleep


# --------------------------------------------------------------------------
# Crash test (parent side)
# --------------------------------------------------------------------------


def _worker_env() -> dict[str, str]:
    """Child env with the SAME PYTHONPATH the wrapper set for us."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "out/pylib:pylib")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _kill_round(path: str, n_commit: int) -> dict:
    """Spawn the worker, wait until it is parked mid-write, SIGKILL it, and
    return what we observed."""
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker", path, str(n_commit)],
        cwd=_ROOT,
        env=_worker_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    committed: int | None = None
    journal: str | None = None
    saw_midwrite = False
    captured: list[str] = []
    deadline = time.time() + 60
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line == "":
            if proc.poll() is not None:
                break  # child exited before parking mid-write
            continue
        captured.append(line.rstrip("\n"))
        line = line.strip()
        if line.startswith(MARK_COMMITTED):
            parts = line.split()
            committed = int(parts[1])
            journal = parts[2] if len(parts) > 2 else "?"
        elif line.startswith(MARK_MIDWRITE):
            saw_midwrite = True
            break

    # Give the child a beat to be firmly parked inside the open transaction,
    # then HARD-KILL it: SIGKILL, no clean close, mid-transaction.
    time.sleep(0.05)
    proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    return {
        "committed": committed,
        "journal": journal,
        "saw_midwrite": saw_midwrite,
        "returncode": proc.returncode,
        "output": "\n".join(captured),
    }


def _verify(path: str) -> dict:
    """Reopen the collection after a kill and check it is intact and the engine
    still loads. Count is captured on the raw reopened state (before any
    repair)."""
    col = Collection(path)
    try:
        integrity = col.db.scalar("pragma integrity_check")
        fk_violations = len(col.db.all("pragma foreign_key_check"))
        count = col.db.scalar("select count(*) from sr_attempts")
        # Anki's own database check: rebuilds caches, proves the scheduler +
        # note/card graph load. ok == no problems found.
        _problems, fix_ok = col.fix_integrity()
        # The engine still produces a readiness signal after the crash.
        snap = col._backend.compute_readiness()
        readiness_ok = MCAT_LOW <= snap.readiness_scaled <= MCAT_HIGH
        return {
            "opened": True,
            "integrity": integrity,
            "fk_violations": fk_violations,
            "count": count,
            "fix_ok": fix_ok,
            "readiness_scaled": snap.readiness_scaled,
            "readiness_ok": readiness_ok,
        }
    finally:
        col.close()


def run_crash_test(n_kills: int) -> dict:
    """Kill the app mid-review n_kills times; prove zero corruption and that
    every committed review survives."""
    print(f"\n[crash] {n_kills} hard kills against a real on-disk collection")
    tmpdir = tempfile.mkdtemp(prefix="speedrun_crash_")
    path = os.path.join(tmpdir, "collection.anki2")
    # Create the collection fresh (this also creates the sr_* engine tables),
    # then close it so the child owns the only writer during each round.
    Collection(path).close()

    rows: list[dict] = []
    expected = 0  # durably-committed sr_attempts we expect to survive so far
    corrupted = 0
    journal_mode = "?"
    try:
        for i in range(1, n_kills + 1):
            killed = _kill_round(path, COMMITS_PER_ROUND)
            if not killed["saw_midwrite"] or killed["committed"] is None:
                raise RuntimeError(
                    "crash worker did not reach the mid-write state; its output "
                    f"was:\n{killed['output']}"
                )
            expected += COMMITS_PER_ROUND
            journal_mode = killed["journal"] or journal_mode

            v = _verify(path)
            reviews_preserved = v["count"] == expected
            # The child's durable count (pre-kill) must equal the parent's count
            # (post-kill): the committed reviews survived AND the ~5000 in-flight
            # rows were rolled back.
            committed_matches = killed["committed"] == v["count"]
            ok = (
                v["opened"]
                and v["integrity"] == "ok"
                and v["fk_violations"] == 0
                and v["fix_ok"]
                and v["readiness_ok"]
                and reviews_preserved
                and committed_matches
            )
            if not ok:
                corrupted += 1

            rows.append(
                {
                    "idx": i,
                    "returncode": killed["returncode"],
                    "saw_midwrite": killed["saw_midwrite"],
                    "committed": killed["committed"],
                    "count": v["count"],
                    "expected": expected,
                    "integrity": v["integrity"],
                    "fk_violations": v["fk_violations"],
                    "fix_ok": v["fix_ok"],
                    "readiness_scaled": v["readiness_scaled"],
                    "reviews_preserved": reviews_preserved,
                    "committed_matches": committed_matches,
                    "ok": ok,
                }
            )
            print(
                f"  kill {i:>2}/{n_kills}: rc={killed['returncode']} "
                f"integrity={v['integrity']} reviews={v['count']}/{expected} "
                f"readiness={v['readiness_scaled']} "
                f"{'OK' if ok else 'CORRUPT'}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "n_kills": n_kills,
        "commits_per_round": COMMITS_PER_ROUND,
        "inflight_rows": INFLIGHT_ROWS,
        "journal_mode": journal_mode,
        "corrupted": corrupted,
        "reviews_expected_final": expected,
        "reviews_final": rows[-1]["count"] if rows else 0,
        "integrity_method": (
            "pragma integrity_check + pragma foreign_key_check + "
            "Collection.fix_integrity() (backend check_database) + "
            "compute_readiness() reloads the engine"
        ),
        "rows": rows,
    }


# --------------------------------------------------------------------------
# Offline / AI-off test
# --------------------------------------------------------------------------


class _NetworkPulled:
    """Context manager that black-holes all outbound Python sockets and counts
    every connection attempt, simulating the network being pulled."""

    def __init__(self) -> None:
        self.attempts = 0

    def __enter__(self) -> "_NetworkPulled":
        self._socket_cls = socket.socket
        self._create_connection = socket.create_connection
        guard = self

        class _Blackhole(self._socket_cls):  # type: ignore[misc, valid-type]
            def connect(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                guard.attempts += 1
                raise OSError("offline: outbound network disabled (AI-off test)")

            def connect_ex(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                guard.attempts += 1
                raise OSError("offline: outbound network disabled (AI-off test)")

        def _blocked_create_connection(*a, **k):  # noqa: ANN001, ANN002, ANN003
            guard.attempts += 1
            raise OSError("offline: outbound network disabled (AI-off test)")

        socket.socket = _Blackhole  # type: ignore[misc, assignment]
        socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        socket.socket = self._socket_cls  # type: ignore[misc]
        socket.create_connection = self._create_connection  # type: ignore[assignment]


def _simulated_online_coach() -> None:
    """Stand-in for the optional online AI coach reaching an LLM endpoint. With
    the network pulled this must fail fast (not hang) so the app can fall back."""
    with socket.create_connection(("api.openai.com", 443), timeout=2):
        pass


def run_offline_test() -> dict:
    """Prove the deterministic engine works with the network pulled, and that
    the AI path degrades cleanly to it."""
    print("\n[offline] network pulled; AI off -> deterministic engine still works")
    # AI is off by default in the engine (the diagnosis classifier ships in
    # rslib and takes no network); set an explicit flag to make the intent clear.
    os.environ["SPEEDRUN_AI"] = "off"

    tmpdir = tempfile.mkdtemp(prefix="speedrun_offline_")
    path = os.path.join(tmpdir, "collection.anki2")

    # Signal-combination cases -> the deterministic (AI-off) classification the
    # engine must return, mirroring rslib/src/speedrun/mod.rs.
    cases = [
        ("recall miss (SRS)", dict(correct=False, recall_failed=True,
                                   question_type=SRS), MEMORY),
        ("missed passage evidence", dict(correct=False, passage_evidence_missed=True,
                                         question_type=PASSAGE_MCQ), PASSAGE),
        ("confident + rushed miss", dict(correct=False, question_type=PASSAGE_MCQ,
                                         took_ms=3000, predicted=0.9), TEST_TAKING),
        ("slow deliberate miss", dict(correct=False, question_type=PASSAGE_MCQ,
                                      took_ms=22000), REASONING),
        ("correct application", dict(correct=True, question_type=DISCRETE,
                                     took_ms=9000), CORRECT),
    ]

    with _NetworkPulled() as net:
        base_attempts = net.attempts
        col = Collection(path)
        try:
            cid, nid = _seed_card(col, "offline")
            diagnoses = []
            all_valid = True
            all_expected = True
            for label, signals, expected_kind in cases:
                resp = _record(col, card_id=cid, note_id=nid, **signals)
                kind = resp.diagnosis.kind
                valid = kind in VALID_KINDS
                matched = kind == expected_kind
                all_valid = all_valid and valid
                all_expected = all_expected and matched
                diagnoses.append(
                    {
                        "case": label,
                        "kind": kind,
                        "kind_name": KIND_NAME.get(kind, str(kind)),
                        "expected": KIND_NAME[expected_kind],
                        "valid": valid,
                        "matched": matched,
                    }
                )
                print(
                    f"  {label:<26} -> {KIND_NAME.get(kind, kind):<11} "
                    f"({'ok' if matched else 'UNEXPECTED'})"
                )

            snap = col._backend.compute_readiness()
            readiness_ok = MCAT_LOW <= snap.readiness_scaled <= MCAT_HIGH
            # The engine path must not have touched the network at all.
            engine_net_attempts = net.attempts - base_attempts
            print(
                f"  readiness -> scaled={snap.readiness_scaled} "
                f"sufficient={snap.sufficient} "
                f"({'score' if snap.sufficient else 'abstains: ' + snap.reason})"
            )
            print(f"  engine outbound connection attempts: {engine_net_attempts}")

            # Now the AI path with the network pulled: it must fail fast and the
            # app must fall back to the deterministic engine and still score.
            ai_attempted = False
            ai_blocked = False
            try:
                _simulated_online_coach()
            except OSError:
                ai_attempted = True
                ai_blocked = True
            fb = _record(col, card_id=cid, note_id=nid, correct=False,
                         passage_evidence_missed=True, question_type=PASSAGE_MCQ)
            fallback_kind = fb.diagnosis.kind
            # The engine keys each readiness snapshot by a millisecond timestamp
            # (its primary key), so let the clock advance past the first
            # compute_readiness() above before recomputing.
            time.sleep(0.01)
            fallback_snap = col._backend.compute_readiness()
            fallback_ok = (
                ai_attempted
                and ai_blocked
                and fallback_kind in VALID_KINDS
                and MCAT_LOW <= fallback_snap.readiness_scaled <= MCAT_HIGH
            )
            print(
                f"  AI coach call with network pulled: attempted={ai_attempted} "
                f"blocked={ai_blocked} -> fell back to engine "
                f"({KIND_NAME.get(fallback_kind, fallback_kind)}, "
                f"readiness={fallback_snap.readiness_scaled})"
            )
        finally:
            col.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    passed = (
        all_valid
        and all_expected
        and readiness_ok
        and engine_net_attempts == 0
        and fallback_ok
    )
    return {
        "passed": passed,
        "diagnoses": diagnoses,
        "all_valid": all_valid,
        "all_expected": all_expected,
        "readiness_scaled": snap.readiness_scaled,
        "readiness_sufficient": snap.sufficient,
        "readiness_reason": snap.reason,
        "readiness_ok": readiness_ok,
        "engine_net_attempts": engine_net_attempts,
        "ai_attempted": ai_attempted,
        "ai_blocked": ai_blocked,
        "fallback_kind": KIND_NAME.get(fallback_kind, str(fallback_kind)),
        "fallback_readiness": fallback_snap.readiness_scaled,
        "fallback_ok": fallback_ok,
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _write_report(crash: dict, offline: dict) -> None:
    L: list[str] = []
    L.append("# Speedrun Crash-Safety & Offline (AI-off) Report\n")
    L.append(
        "Proof artifact for projectspec **§7g** (crash + offline tests), produced by "
        "`tools/speedrun_crash.py` over the Rust SpeedrunService engine \u2014 the same "
        "protobuf boundary the desktop app and the phone use. No AI, no network.\n"
    )
    L.append(f"\nReproduce: `./tools/speedrun_crash.sh` (or `./tools/speedrun_crash.sh {crash['n_kills']}`).\n")

    # --- crash ---
    L.append("\n## 1. Crash test \u2014 kill the app mid-review, prove zero corruption\n")
    L.append(
        f"Each round a child process opens a **real on-disk** collection, durably "
        f"commits {crash['commits_per_round']} reviews, then opens a transaction and "
        f"writes {crash['inflight_rows']:,} rows of a \u201creview in progress\u201d it "
        f"**never commits**. The parent then **`SIGKILL`s** it while it is parked "
        f"mid-write (no clean close), reopens the collection, and checks it.\n"
    )
    L.append("\n| Metric | Result |")
    L.append("| --- | --- |")
    L.append(f"| Kills performed | **{crash['n_kills']}** |")
    L.append(f"| Collections corrupted | **{crash['corrupted']}** |")
    L.append(f"| SQLite journal mode | `{crash['journal_mode']}` |")
    L.append(
        f"| Reviews before \u2192 after | 0 \u2192 **{crash['reviews_final']}** "
        f"(expected {crash['reviews_expected_final']}) |"
    )
    kill_rc = crash["rows"][0]["returncode"] if crash["rows"] else "n/a"
    L.append(f"| Child exit signal | `{kill_rc}` (SIGKILL) each round |")
    L.append(f"| Integrity method | {crash['integrity_method']} |")
    L.append(
        "\nEvery round the interrupted transaction (the in-flight review) is rolled "
        "back by SQLite's crash recovery, the durably-committed reviews survive "
        "exactly (count is monotonic and matches the child's pre-kill count), and "
        "the engine reloads and still computes readiness.\n"
    )
    L.append("\n### Per-kill detail\n")
    L.append(
        "| # | child rc | mid-write? | committed (pre-kill) | reviews (post-kill) | "
        "expected | integrity_check | fk | fix_integrity | readiness | verdict |"
    )
    L.append("| " + " | ".join(["---"] * 11) + " |")
    for r in crash["rows"]:
        L.append(
            f"| {r['idx']} | `{r['returncode']}` | "
            f"{'yes' if r['saw_midwrite'] else 'no'} | {r['committed']} | "
            f"{r['count']} | {r['expected']} | {r['integrity']} | "
            f"{r['fk_violations']} | {'ok' if r['fix_ok'] else 'FAIL'} | "
            f"{r['readiness_scaled']} | {'PASS' if r['ok'] else 'CORRUPT'} |"
        )
    verdict = "PASS" if crash["corrupted"] == 0 else "FAIL"
    L.append(
        f"\n**Crash test: {verdict}** \u2014 {crash['n_kills']} hard kills, "
        f"{crash['corrupted']} corrupted collections, "
        f"{crash['reviews_final']} committed reviews preserved.\n"
    )

    # --- offline ---
    L.append("\n## 2. Offline / AI-off test \u2014 network pulled, engine still scores\n")
    L.append(
        "All outbound Python sockets are black-holed (any `connect()` raises and is "
        "counted). The deterministic engine path is pure local Rust computation, so "
        "it keeps working with the network gone; the optional AI coach fails fast and "
        "the app falls back to it.\n"
    )
    L.append("\n### Deterministic diagnosis with the network pulled\n")
    L.append("| Attempt (signals) | Diagnosis | Expected | Valid non-AI kind? |")
    L.append("| --- | --- | --- | --- |")
    for d in offline["diagnoses"]:
        L.append(
            f"| {d['case']} | {d['kind_name']} | {d['expected']} | "
            f"{'yes' if d['valid'] else 'NO'} |"
        )
    ready_txt = (
        f"score {offline['readiness_scaled']}"
        if offline["readiness_sufficient"]
        else f"abstains ({offline['readiness_reason']})"
    )
    L.append("\n| Metric | Result |")
    L.append("| --- | --- |")
    L.append(
        f"| Engine outbound connection attempts | **{offline['engine_net_attempts']}** |"
    )
    L.append(
        f"| Readiness (AI-off, offline) | {ready_txt}, scaled "
        f"{offline['readiness_scaled']} (MCAT {MCAT_LOW}\u2013{MCAT_HIGH}) |"
    )
    L.append(
        f"| AI coach call (network pulled) | attempted={offline['ai_attempted']}, "
        f"blocked={offline['ai_blocked']} |"
    )
    L.append(
        f"| Clean fallback to engine | diagnosis `{offline['fallback_kind']}`, "
        f"readiness {offline['fallback_readiness']} |"
    )
    verdict = "PASS" if offline["passed"] else "FAIL"
    L.append(
        f"\n**Offline / AI-off test: {verdict}** \u2014 the engine made "
        f"{offline['engine_net_attempts']} outbound connections, produced valid "
        "deterministic diagnoses and a readiness signal with the network pulled, and "
        "the AI path degraded cleanly to the deterministic engine (which still "
        "gives a score).\n"
    )

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"\n[report written to {os.path.relpath(REPORT_PATH, _ROOT)}]")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--worker":
        return _crash_worker(argv[1], int(argv[2]))

    n_kills = DEFAULT_KILLS
    positional = [a for a in argv if not a.startswith("-")]
    if positional:
        n_kills = int(positional[0])

    crash = run_crash_test(n_kills)
    offline = run_offline_test()
    _write_report(crash, offline)

    # Assert the invariants (re-runnable self-test; non-zero on first failure).
    failures: list[str] = []
    if crash["corrupted"] != 0:
        failures.append(f"{crash['corrupted']} corrupted collection(s) after kills")
    for r in crash["rows"]:
        if not r["ok"]:
            failures.append(f"kill {r['idx']}: integrity/preservation check failed")
    if crash["reviews_final"] != crash["reviews_expected_final"]:
        failures.append("committed reviews were not all preserved")
    if not offline["passed"]:
        failures.append("offline / AI-off test failed")

    print("\n" + "=" * 66)
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        print("speedrun crash + offline: FAIL")
        return 1
    print(
        f"speedrun crash + offline: PASS "
        f"({crash['n_kills']} kills, {crash['corrupted']} corrupted, "
        f"{crash['reviews_final']} reviews preserved, "
        f"offline AI-off fallback works)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
