-- Migration 007: advisory Review language coaching (AUD-77); backend-only access, additive, no existing table changes.

BEGIN;

CREATE TABLE review_improvements (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	attempt_id UUID NOT NULL,
	status VARCHAR(12) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	improved_text TEXT,
	notes JSONB,
	rejection VARCHAR(40),
	model VARCHAR(60),
	prompt_version VARCHAR(20) NOT NULL,
	claimed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT improvement_status_valid CHECK (status IN ('pending','ready','rejected','failed','abandoned')),
	CONSTRAINT improvement_ready_complete CHECK (status <> 'ready' OR (improved_text IS NOT NULL AND notes IS NOT NULL)),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id)
);

CREATE INDEX improvements_by_learner_claim ON review_improvements (user_id, claimed_at);

CREATE UNIQUE INDEX one_live_improvement_per_exercise ON review_improvements (exercise_id) WHERE status IN ('pending', 'ready', 'rejected');

ALTER TABLE review_improvements ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE review_improvements FROM PUBLIC;

DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON review_improvements FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON review_improvements FROM authenticated; END IF; END $audli$;

INSERT INTO schema_migrations(version, applied_at) VALUES (7, now());

COMMIT;
