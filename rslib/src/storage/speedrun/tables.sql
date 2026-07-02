-- Speedrun evidence tables. Created idempotently on collection open so the
-- Anki collection schema version (and its sync/sanity machinery) is untouched.
CREATE TABLE IF NOT EXISTS sr_attempts (
  id integer PRIMARY KEY NOT NULL,
  cid integer NOT NULL,
  nid integer NOT NULL,
  session_id text NOT NULL DEFAULT '',
  answered_at_ms integer NOT NULL,
  took_ms integer NOT NULL DEFAULT 0,
  -- 0=srs review, 1=passage mcq, 2=discrete mcq
  question_type integer NOT NULL DEFAULT 0,
  selected integer,
  correct integer NOT NULL DEFAULT 0,
  -- embedded diagnosis/action (v1): 0=none,1=memory,2=reasoning,3=passage,4=test_taking,5=correct
  diagnosis_kind integer NOT NULL DEFAULT 0,
  diagnosis_confidence real NOT NULL DEFAULT 0.0,
  -- 0=none,1=resurface,2=passage_practice,3=strategy,4=advance
  routed_action integer NOT NULL DEFAULT 0,
  -- 0=pending,1=accepted,2=dismissed,3=completed
  action_status integer NOT NULL DEFAULT 0,
  usn integer NOT NULL DEFAULT -1,
  data text NOT NULL DEFAULT '',
  -- model's pre-answer predicted probability of a correct/recall outcome
  -- (0..1); NULL when no prediction was captured. Used for calibration.
  predicted real
);
CREATE INDEX IF NOT EXISTS ix_sr_attempts_cid ON sr_attempts (cid);
CREATE INDEX IF NOT EXISTS ix_sr_attempts_usn ON sr_attempts (usn);

CREATE TABLE IF NOT EXISTS sr_readiness (
  id integer PRIMARY KEY NOT NULL,
  computed_at_ms integer NOT NULL,
  memory real NOT NULL DEFAULT 0.0,
  performance real NOT NULL DEFAULT 0.0,
  recall_perf_gap real NOT NULL DEFAULT 0.0,
  coverage real NOT NULL DEFAULT 0.0,
  readiness_scaled integer NOT NULL DEFAULT 0,
  low_scaled integer NOT NULL DEFAULT 0,
  high_scaled integer NOT NULL DEFAULT 0,
  -- 1 if the give-up rule was satisfied (enough evidence to trust the number)
  sufficient integer NOT NULL DEFAULT 0,
  reason text NOT NULL DEFAULT '',
  -- per-dimension sufficiency + the dimension currently blocking confidence
  memory_sufficient integer NOT NULL DEFAULT 0,
  performance_sufficient integer NOT NULL DEFAULT 0,
  blocking_dimension text NOT NULL DEFAULT 'none'
);

CREATE TABLE IF NOT EXISTS sr_topic_map (
  topic text PRIMARY KEY NOT NULL COLLATE unicase,
  label text NOT NULL DEFAULT '',
  weight real NOT NULL DEFAULT 1.0
);

-- Single-row exam profile (exam date + target score) driving exam-anchored
-- scheduling. Replaces FSRS "desired retention" as the primary user input.
CREATE TABLE IF NOT EXISTS sr_profile (
  id integer PRIMARY KEY NOT NULL,
  exam_date_ms integer,
  target_score integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sr_question_items (
  id integer PRIMARY KEY NOT NULL,
  cid integer,
  topic text NOT NULL DEFAULT '',
  -- 0=hand_authored,1=open_licensed,2=ai_generated
  provenance integer NOT NULL DEFAULT 0,
  payload text NOT NULL DEFAULT ''
);
