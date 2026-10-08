-- Migration 002: structured conversational lessons.

BEGIN;

CREATE TABLE lesson_lifecycles (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	status VARCHAR(12) NOT NULL,
	revision INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	data JSONB NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT lesson_owner_key UNIQUE (id, user_id),
	CONSTRAINT lesson_status_valid CHECK (status IN ('active','paused','completed')),
	CONSTRAINT lesson_revision_valid CHECK (revision >= 0),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX lessons_by_learner_time ON lesson_lifecycles (user_id, started_at);

CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status != 'completed';

ALTER TABLE lesson_lifecycles ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE lesson_lifecycles FROM PUBLIC;

CREATE TABLE lesson_exercises (
	exercise_id UUID NOT NULL,
	lesson_id UUID NOT NULL,
	user_id UUID NOT NULL,
	PRIMARY KEY (exercise_id),
	CONSTRAINT lesson_exercise_owner FOREIGN KEY(lesson_id, user_id) REFERENCES lesson_lifecycles (id, user_id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE INDEX exercises_by_lesson ON lesson_exercises (lesson_id);

ALTER TABLE lesson_exercises ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE lesson_exercises FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE lesson_lifecycles, lesson_exercises FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE lesson_lifecycles, lesson_exercises FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO schema_migrations(version, applied_at) VALUES (2, now());

COMMIT;
