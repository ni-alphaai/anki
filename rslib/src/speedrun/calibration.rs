// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Calibration of predicted probabilities against actual outcomes (AI-off).
//!
//! When an attempt captures the model's pre-answer probability of success, we
//! can measure whether those probabilities are honest: Brier score and log loss
//! summarise calibration, and a reliability curve (binned) shows where the
//! model is over- or under-confident. Lower Brier/log-loss is better.

/// One (predicted probability, actual outcome) pair.
#[derive(Debug, Clone, Copy)]
pub struct CalibrationPair {
    pub predicted: f32,
    pub outcome: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CalibrationBin {
    pub lo: f32,
    pub hi: f32,
    pub count: u32,
    pub mean_predicted: f32,
    pub mean_outcome: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CalibrationReport {
    pub n: u32,
    pub brier: f32,
    pub log_loss: f32,
    pub sufficient: bool,
    pub note: String,
    pub bins: Vec<CalibrationBin>,
}

/// Minimum predictions before calibration is worth reporting.
pub const MIN_PREDICTIONS: u32 = 20;

const LOG_EPS: f32 = 1e-7;

/// Compute calibration over the given pairs using `n_bins` equal-width buckets
/// across [0, 1].
pub fn compute_calibration(pairs: &[CalibrationPair], n_bins: usize) -> CalibrationReport {
    let n = pairs.len() as u32;
    let n_bins = n_bins.max(1);

    let mut brier_sum = 0.0f32;
    let mut log_loss_sum = 0.0f32;
    let mut bin_count = vec![0u32; n_bins];
    let mut bin_pred = vec![0.0f32; n_bins];
    let mut bin_out = vec![0.0f32; n_bins];

    for pair in pairs {
        let p = pair.predicted.clamp(0.0, 1.0);
        let y = if pair.outcome { 1.0 } else { 0.0 };

        brier_sum += (p - y) * (p - y);
        let pc = p.clamp(LOG_EPS, 1.0 - LOG_EPS);
        log_loss_sum += -(y * pc.ln() + (1.0 - y) * (1.0 - pc).ln());

        let mut idx = (p * n_bins as f32) as usize;
        if idx >= n_bins {
            idx = n_bins - 1; // p == 1.0 lands in the last bin
        }
        bin_count[idx] += 1;
        bin_pred[idx] += p;
        bin_out[idx] += y;
    }

    let (brier, log_loss) = if n > 0 {
        (brier_sum / n as f32, log_loss_sum / n as f32)
    } else {
        (0.0, 0.0)
    };

    let bins = (0..n_bins)
        .map(|i| {
            let count = bin_count[i];
            let (mean_predicted, mean_outcome) = if count > 0 {
                (bin_pred[i] / count as f32, bin_out[i] / count as f32)
            } else {
                (0.0, 0.0)
            };
            CalibrationBin {
                lo: i as f32 / n_bins as f32,
                hi: (i + 1) as f32 / n_bins as f32,
                count,
                mean_predicted,
                mean_outcome,
            }
        })
        .collect();

    let sufficient = n >= MIN_PREDICTIONS;
    let note = if sufficient {
        "calibration computed".to_string()
    } else {
        format!("not enough predictions: {n}/{MIN_PREDICTIONS}")
    };

    CalibrationReport {
        n,
        brier,
        log_loss,
        sufficient,
        note,
        bins,
    }
}

#[cfg(test)]
mod test {
    use super::*;

    fn pair(predicted: f32, outcome: bool) -> CalibrationPair {
        CalibrationPair { predicted, outcome }
    }

    #[test]
    fn empty_is_zeroed() {
        let report = compute_calibration(&[], 10);
        assert_eq!(report.n, 0);
        assert_eq!(report.brier, 0.0);
        assert!(!report.sufficient);
    }

    #[test]
    fn perfect_predictions_have_zero_brier() {
        let pairs = [pair(1.0, true), pair(0.0, false), pair(1.0, true)];
        let report = compute_calibration(&pairs, 10);
        assert!(report.brier.abs() < 1e-6);
        assert!(report.log_loss < 1e-3);
    }

    #[test]
    fn constant_half_predictions_have_quarter_brier() {
        let pairs = [pair(0.5, true), pair(0.5, false)];
        let report = compute_calibration(&pairs, 10);
        // each term is (0.5)^2 = 0.25
        assert!((report.brier - 0.25).abs() < 1e-6);
    }

    #[test]
    fn worst_predictions_have_brier_one() {
        let pairs = [pair(0.0, true), pair(1.0, false)];
        let report = compute_calibration(&pairs, 10);
        assert!((report.brier - 1.0).abs() < 1e-6);
    }

    #[test]
    fn bins_capture_counts() {
        let pairs = [pair(0.05, false), pair(0.95, true), pair(0.95, true)];
        let report = compute_calibration(&pairs, 10);
        assert_eq!(report.bins.len(), 10);
        assert_eq!(report.bins[0].count, 1);
        assert_eq!(report.bins[9].count, 2);
        assert!((report.bins[9].mean_outcome - 1.0).abs() < 1e-6);
    }
}
