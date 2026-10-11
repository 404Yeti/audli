-- Migration 003: fleet-wide accent admission controls; initially disabled.

BEGIN;

CREATE TABLE accent_control (
	id INTEGER NOT NULL,
	enabled INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	daily_limit INTEGER NOT NULL,
	concurrent_limit INTEGER NOT NULL,
	budget_day VARCHAR(10) NOT NULL,
	characters INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT accent_control_singleton CHECK (id=1 AND enabled IN (0,1) AND revision>=0),
	CONSTRAINT accent_control_limits CHECK (daily_limit>=0 AND characters>=0 AND concurrent_limit BETWEEN 1 AND 16)
);

ALTER TABLE accent_control ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE accent_control FROM PUBLIC;

CREATE TABLE accent_reservations (
	scope VARCHAR(64) NOT NULL,
	token UUID NOT NULL,
	budget_day VARCHAR(10) NOT NULL,
	characters INTEGER NOT NULL,
	flag_revision INTEGER NOT NULL,
	state VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (scope),
	CONSTRAINT accent_reservation_characters CHECK (characters BETWEEN 1 AND 4000),
	CONSTRAINT accent_reservation_state CHECK (state IN ('active','success','failed','cancelled','abandoned')),
	UNIQUE (token)
);

CREATE INDEX accent_reservations_by_lease ON accent_reservations (lease_until);

CREATE INDEX accent_reservations_by_state_lease ON accent_reservations (state, lease_until);

ALTER TABLE accent_reservations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE accent_reservations FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE accent_control, accent_reservations FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE accent_control, accent_reservations FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO accent_control VALUES (1,0,0,0,2,to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD'),0,now());

INSERT INTO schema_migrations(version, applied_at) VALUES (3, now());

COMMIT;
