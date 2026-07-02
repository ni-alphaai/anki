// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Exam-anchored scheduling (AI-off).
//!
//! The brainlift's scheduling thesis: anchor study to an **exam date + target
//! score tier** rather than an abstract retention rate, project a readiness
//! trajectory toward the target, and switch to a consolidation mode when the
//! trajectory demands it. This module is the pure decision layer; the service
//! gathers the live readiness + profile and calls in here.

pub const SCALE_LOW: u32 = 472;
pub const SCALE_HIGH: u32 = 528;

/// Days-left threshold below which we switch to consolidation when not yet at
/// target.
pub const CONSOLIDATION_WINDOW_DAYS: i64 = 28;

#[derive(Debug, Clone, Copy)]
pub struct ExamPlanInputs {
    pub has_profile: bool,
    /// Current readiness on the MCAT scale.
    pub current_readiness: u32,
    /// Target score (472..=528); 0 = unset.
    pub target_score: u32,
    /// Days until the exam; negative = unknown/no date.
    pub days_left: i64,
    /// Whether the readiness estimate itself has enough evidence.
    pub readiness_sufficient: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExamPlanReport {
    pub has_profile: bool,
    pub days_left: i64,
    pub current_readiness: u32,
    pub target_score: u32,
    pub on_track: bool,
    pub needed_points: u32,
    pub points_per_week_needed: f32,
    pub study_mode: String,
    pub recommended_tier: String,
    pub readiness_sufficient: bool,
    pub note: String,
}

/// Tier label for a score (MCAT population bands used in the brainlift).
pub fn tier_label(score: u32) -> &'static str {
    match score {
        s if s >= 505 => "505-528 (top third)",
        s if s >= 495 => "495-504 (middle third)",
        s if s >= SCALE_LOW => "472-494 (bottom third)",
        _ => "unset",
    }
}

/// Recommend a realistic next target band given current readiness.
fn recommend_tier(current_readiness: u32) -> &'static str {
    if current_readiness < 495 {
        "495-504 (middle third)"
    } else if current_readiness < 505 {
        "505-528 (top third)"
    } else {
        "505-528 (top third, maintain)"
    }
}

pub fn compute_exam_plan(inputs: &ExamPlanInputs) -> ExamPlanReport {
    let needed_points = inputs.target_score.saturating_sub(inputs.current_readiness);
    let on_track = inputs.target_score > 0 && inputs.current_readiness >= inputs.target_score;

    let weeks_left = if inputs.days_left > 0 {
        inputs.days_left as f32 / 7.0
    } else {
        0.0
    };
    let points_per_week_needed = if weeks_left > 0.0 {
        needed_points as f32 / weeks_left
    } else {
        needed_points as f32
    };

    let study_mode = if !inputs.has_profile {
        "long_term"
    } else if on_track {
        "maintenance"
    } else if inputs.days_left >= 0 && inputs.days_left <= CONSOLIDATION_WINDOW_DAYS {
        "consolidation"
    } else {
        "long_term"
    };

    let note = if !inputs.has_profile {
        "no exam profile set; defaulting to long-term spacing".to_string()
    } else if !inputs.readiness_sufficient {
        "readiness evidence is thin; plan is provisional".to_string()
    } else if on_track {
        "at or above target; consolidate and maintain".to_string()
    } else {
        format!("need +{needed_points} points to reach target")
    };

    ExamPlanReport {
        has_profile: inputs.has_profile,
        days_left: inputs.days_left,
        current_readiness: inputs.current_readiness,
        target_score: inputs.target_score,
        on_track,
        needed_points,
        points_per_week_needed,
        study_mode: study_mode.to_string(),
        recommended_tier: recommend_tier(inputs.current_readiness).to_string(),
        readiness_sufficient: inputs.readiness_sufficient,
        note,
    }
}

#[cfg(test)]
mod test {
    use super::*;

    fn inputs(has_profile: bool, current: u32, target: u32, days_left: i64) -> ExamPlanInputs {
        ExamPlanInputs {
            has_profile,
            current_readiness: current,
            target_score: target,
            days_left,
            readiness_sufficient: true,
        }
    }

    #[test]
    fn no_profile_defaults_to_long_term() {
        let plan = compute_exam_plan(&inputs(false, 480, 0, -1));
        assert_eq!(plan.study_mode, "long_term");
        assert!(!plan.on_track);
    }

    #[test]
    fn near_exam_below_target_consolidates() {
        let plan = compute_exam_plan(&inputs(true, 500, 510, 14));
        assert_eq!(plan.study_mode, "consolidation");
        assert_eq!(plan.needed_points, 10);
        assert!(!plan.on_track);
        // 14 days = 2 weeks -> 5 points/week
        assert!((plan.points_per_week_needed - 5.0).abs() < 1e-6);
    }

    #[test]
    fn at_target_is_maintenance() {
        let plan = compute_exam_plan(&inputs(true, 515, 510, 40));
        assert_eq!(plan.study_mode, "maintenance");
        assert!(plan.on_track);
        assert_eq!(plan.needed_points, 0);
    }

    #[test]
    fn far_from_exam_stays_long_term() {
        let plan = compute_exam_plan(&inputs(true, 490, 510, 120));
        assert_eq!(plan.study_mode, "long_term");
    }

    #[test]
    fn tier_labels_map_to_bands() {
        assert_eq!(tier_label(528), "505-528 (top third)");
        assert_eq!(tier_label(500), "495-504 (middle third)");
        assert_eq!(tier_label(480), "472-494 (bottom third)");
        assert_eq!(tier_label(0), "unset");
    }
}
