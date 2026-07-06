// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Speedrun's diagnostic evidence engine.
//!
//! This module owns the deterministic (AI-free) classification of a missed
//! question into a root-cause failure mode, plus the routed next action. The
//! optional AI path lives outside the Rust core (see the diagnosis
//! orchestration boundary in speedrun_architecture_research.md); this module is
//! always available offline and with AI disabled.

pub mod calibration;
pub mod coverage;
pub mod exam;
pub mod feedback;
pub mod interleave;
pub mod leakage;
pub(crate) mod notesync;
pub mod performance;
pub mod points_at_stake;
pub mod readiness;
pub mod reasoning_round;
pub mod reasoning_schedule;
pub(crate) mod service;

// Diagnosis kinds, mirroring sr_attempts.diagnosis_kind.
pub const DIAGNOSIS_NONE: u8 = 0;
pub const DIAGNOSIS_MEMORY: u8 = 1;
pub const DIAGNOSIS_REASONING: u8 = 2;
pub const DIAGNOSIS_PASSAGE: u8 = 3;
pub const DIAGNOSIS_TEST_TAKING: u8 = 4;
pub const DIAGNOSIS_CORRECT: u8 = 5;

// Routed actions, mirroring sr_attempts.routed_action.
pub const ACTION_NONE: u8 = 0;
pub const ACTION_RESURFACE: u8 = 1;
pub const ACTION_PASSAGE_PRACTICE: u8 = 2;
pub const ACTION_STRATEGY: u8 = 3;
pub const ACTION_ADVANCE: u8 = 4;

/// The result of classifying an attempt.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Diagnosis {
    pub kind: u8,
    pub confidence: f32,
    pub routed_action: u8,
}

/// A wrong exam-style answer counts as a test-taking (careless/rushed) miss
/// only when the student was this confident or more -- otherwise it is a
/// reasoning gap.
pub const TEST_TAKING_CONFIDENCE: f32 = 0.75;

/// Inputs to the deterministic classifier.
#[derive(Debug, Clone, Copy, Default)]
pub struct AttemptSignals {
    pub correct: bool,
    pub took_ms: u32,
    /// The student could not recall the underlying fact.
    pub recall_failed: bool,
    /// The student missed or misread relevant passage evidence.
    pub passage_evidence_missed: bool,
    /// 0=srs review, 1=passage mcq, 2=discrete mcq.
    pub question_type: u8,
    /// Pre-answer confidence the student reported, 0.0..=1.0 (0 = unknown).
    pub confidence: f32,
}

/// Classify a miss into a root-cause failure mode and a routed repair action.
///
/// This is intentionally simple and rule-based for v1: it is the AI-off
/// fallback that the architecture requires, and a baseline the AI path must
/// beat in evaluation.
pub fn classify(signals: &AttemptSignals) -> Diagnosis {
    if signals.correct {
        return Diagnosis {
            kind: DIAGNOSIS_CORRECT,
            confidence: 1.0,
            routed_action: ACTION_ADVANCE,
        };
    }
    if signals.recall_failed {
        return Diagnosis {
            kind: DIAGNOSIS_MEMORY,
            confidence: 0.8,
            routed_action: ACTION_RESURFACE,
        };
    }
    if signals.passage_evidence_missed {
        return Diagnosis {
            kind: DIAGNOSIS_PASSAGE,
            confidence: 0.7,
            routed_action: ACTION_PASSAGE_PRACTICE,
        };
    }
    // Test-taking gap = a *confident* wrong answer given quickly on an exam-style
    // question: the student was sure but careless/misread, not lacking the concept.
    // A fast wrong answer with low/unknown confidence is treated as a reasoning gap
    // (they didn't actually have it), which is the safer default.
    if signals.question_type != 0
        && signals.took_ms > 0
        && signals.took_ms < 8_000
        && signals.confidence >= TEST_TAKING_CONFIDENCE
    {
        return Diagnosis {
            kind: DIAGNOSIS_TEST_TAKING,
            confidence: 0.5,
            routed_action: ACTION_STRATEGY,
        };
    }
    // Knew the fact but applied it incorrectly.
    Diagnosis {
        kind: DIAGNOSIS_REASONING,
        confidence: 0.6,
        routed_action: ACTION_PASSAGE_PRACTICE,
    }
}

#[cfg(test)]
mod test {
    use super::*;

    fn signals(correct: bool, took_ms: u32, recall_failed: bool, passage: bool) -> AttemptSignals {
        AttemptSignals {
            correct,
            took_ms,
            recall_failed,
            passage_evidence_missed: passage,
            question_type: 1,
            confidence: 0.0,
        }
    }

    #[test]
    fn classifier_routes_each_failure_mode() {
        assert_eq!(
            classify(&signals(true, 5000, false, false)).kind,
            DIAGNOSIS_CORRECT
        );
        assert_eq!(
            classify(&signals(false, 5000, true, false)).kind,
            DIAGNOSIS_MEMORY
        );
        assert_eq!(
            classify(&signals(false, 5000, false, true)).kind,
            DIAGNOSIS_PASSAGE
        );
        // fast + confident but wrong -> test-taking (careless)
        let mut confident_rush = signals(false, 3000, false, false);
        confident_rush.confidence = 0.85;
        assert_eq!(classify(&confident_rush).kind, DIAGNOSIS_TEST_TAKING);
        // fast but NOT confident -> reasoning (didn't actually have it)
        assert_eq!(
            classify(&signals(false, 3000, false, false)).kind,
            DIAGNOSIS_REASONING
        );
        // slow but wrong, knew it -> reasoning
        assert_eq!(
            classify(&signals(false, 20000, false, false)).kind,
            DIAGNOSIS_REASONING
        );
    }

    #[test]
    fn correct_advances_with_full_confidence() {
        let d = classify(&signals(true, 1000, false, false));
        assert_eq!(d.routed_action, ACTION_ADVANCE);
        assert_eq!(d.confidence, 1.0);
    }
}
