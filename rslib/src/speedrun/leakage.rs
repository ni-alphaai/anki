// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Leakage check for the held-out question bank (AI-off).
//!
//! The paraphrase test only means something if the held-out questions are not
//! verbatim copies of the cards the student already studies. This flags a
//! question item as leaked when its (normalized) stem appears inside the
//! linked source card's note text - i.e., it is not actually a reworded item.

/// Lowercase, keep alphanumerics, collapse everything else to single spaces.
pub fn normalize(text: &str) -> String {
    let mut out = String::new();
    let mut prev_space = false;
    for ch in text.chars() {
        if ch.is_alphanumeric() {
            for lower in ch.to_lowercase() {
                out.push(lower);
            }
            prev_space = false;
        } else if !out.is_empty() && !prev_space {
            out.push(' ');
            prev_space = true;
        }
    }
    out.trim().to_string()
}

/// Minimum normalized stem length to consider; shorter stems are too generic
/// to treat as leakage.
pub const MIN_STEM_LEN: usize = 12;

/// True when the stem looks like a verbatim copy of the note text.
pub fn is_leaked(stem: &str, note_text: &str) -> bool {
    let stem_n = normalize(stem);
    if stem_n.len() < MIN_STEM_LEN {
        return false;
    }
    normalize(note_text).contains(&stem_n)
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn normalizes_punctuation_and_case() {
        assert_eq!(
            normalize("The pKa, of Amino-acids!"),
            "the pka of amino acids"
        );
    }

    #[test]
    fn identical_stem_is_leaked() {
        let note = "The peptide bond is an amide bond between residues.";
        let stem = "the peptide bond is an amide bond";
        assert!(is_leaked(stem, note));
    }

    #[test]
    fn reworded_stem_is_not_leaked() {
        let note = "The peptide bond is an amide bond between residues.";
        let stem = "Which functional group links adjacent amino acids in a protein?";
        assert!(!is_leaked(stem, note));
    }

    #[test]
    fn short_stem_is_ignored() {
        let note = "Amino acids are the building blocks of proteins.";
        assert!(!is_leaked("amino", note));
    }
}
