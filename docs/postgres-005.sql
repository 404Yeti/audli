-- Migration 005: optional listening homework; backend-only access.

BEGIN;

CREATE TABLE listening_content (
	id UUID NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	provider_id VARCHAR(100) NOT NULL, 
	data JSONB NOT NULL, 
	validated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT content_provider_key UNIQUE (provider, provider_id)
);

ALTER TABLE listening_content ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_content FROM PUBLIC;

CREATE TABLE homework_assignments (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	lesson_id UUID NOT NULL, 
	content_id UUID, 
	status VARCHAR(16) NOT NULL, 
	objective TEXT NOT NULL, 
	topic VARCHAR(100), 
	attempts INTEGER NOT NULL, 
	lease_token UUID, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT homework_lesson_owner FOREIGN KEY(lesson_id, user_id) REFERENCES lesson_lifecycles (id, user_id), 
	CONSTRAINT one_homework_per_lesson UNIQUE (user_id, lesson_id), 
	CONSTRAINT no_repeated_homework_content UNIQUE (user_id, content_id), 
	CONSTRAINT homework_owner_key UNIQUE (id, user_id), 
	CONSTRAINT homework_status_valid CHECK (status IN ('offered','assigned','completed','discussed','skipped','unavailable')), 
	CONSTRAINT homework_attempts_bounded CHECK (attempts BETWEEN 0 AND 2), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(content_id) REFERENCES listening_content (id)
);

CREATE INDEX homework_by_owner_time ON homework_assignments (user_id, created_at);

ALTER TABLE homework_assignments ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE homework_assignments FROM PUBLIC;

CREATE TABLE homework_reflections (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	assignment_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	data JSONB NOT NULL, 
	grounding VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT reflection_owner FOREIGN KEY(assignment_id, user_id) REFERENCES homework_assignments (id, user_id), 
	CONSTRAINT reflection_grounding_valid CHECK (grounding='unverified_self_report')
);

CREATE INDEX reflections_by_assignment_owner ON homework_reflections (assignment_id, user_id);

ALTER TABLE homework_reflections ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE homework_reflections FROM PUBLIC;

CREATE TABLE homework_discovery_budget (
	id INTEGER NOT NULL, 
	day VARCHAR(10) NOT NULL, 
	searches INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT homework_budget_valid CHECK (id=1 AND searches>=0)
);

ALTER TABLE homework_discovery_budget ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE homework_discovery_budget FROM PUBLIC;

DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON listening_content, homework_assignments, homework_reflections, homework_discovery_budget FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON listening_content, homework_assignments, homework_reflections, homework_discovery_budget FROM authenticated; END IF; END $audli$;

INSERT INTO schema_migrations(version, applied_at) VALUES (5, now());

COMMIT;
