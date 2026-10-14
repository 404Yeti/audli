-- Migration 004: owned adaptive roadmaps.

BEGIN;

CREATE TABLE learner_roadmaps (
	user_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	data JSONB NOT NULL, 
	PRIMARY KEY (user_id), 
	CONSTRAINT roadmap_revision_valid CHECK (revision >= 0), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

ALTER TABLE learner_roadmaps ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE learner_roadmaps FROM PUBLIC;

DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON learner_roadmaps FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON learner_roadmaps FROM authenticated; END IF; END $audli$;

INSERT INTO schema_migrations(version, applied_at) VALUES (4, now());

COMMIT;
