-- Generated from hosting/schema.py. Initial schema only; no user data is removed.

-- UTC timestamps are DOUBLE Unix seconds; all public BIGINT IDs are strings.


CREATE TABLE agent_runs (
	id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	workspace_id BIGINT NOT NULL, 
	session_id BIGINT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	idempotency_key VARCHAR(100) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	input_ref VARCHAR(255) NOT NULL, 
	resumed_from_run_id BIGINT, 
	worker_id VARCHAR(80), 
	lease_until DOUBLE, 
	fence_token INTEGER NOT NULL, 
	deadline_at DOUBLE, 
	queue_deadline DOUBLE NOT NULL, 
	next_dispatch_at DOUBLE NOT NULL, 
	cancel_requested_at DOUBLE, 
	stop_reason VARCHAR(80), 
	error_code VARCHAR(80), 
	checkpoint_ref VARCHAR(255), 
	workspace_version INTEGER NOT NULL, 
	event_seq INTEGER NOT NULL, 
	output_bytes BIGINT NOT NULL, 
	started_at DOUBLE, 
	finished_at DOUBLE, 
	trace_ref VARCHAR(255), 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_run_idempotency UNIQUE (user_id, idempotency_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_run_dispatch ON agent_runs (status, next_dispatch_at, id);

CREATE INDEX ix_run_lease ON agent_runs (status, lease_until, id);

CREATE INDEX ix_run_session ON agent_runs (session_id, id);

CREATE INDEX ix_run_user_state ON agent_runs (user_id, status, id);


CREATE TABLE agent_sessions (
	id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	workspace_id BIGINT NOT NULL, 
	title VARCHAR(120), 
	status VARCHAR(20), 
	active_run_id BIGINT, 
	next_seq BIGINT NOT NULL, 
	version INTEGER NOT NULL, 
	updated_at DOUBLE NOT NULL, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_session_user_page ON agent_sessions (user_id, updated_at, id);

CREATE INDEX ix_session_workspace_page ON agent_sessions (workspace_id, updated_at, id);


CREATE TABLE approvals (
	id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	workspace_id BIGINT, 
	session_id BIGINT, 
	run_id BIGINT, 
	confirmation_id VARCHAR(100), 
	action_name VARCHAR(80), 
	args_hash VARCHAR(64), 
	status VARCHAR(24), 
	expires_at DOUBLE, 
	decided_by BIGINT, 
	consumed_at DOUBLE, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_approval_expiry ON approvals (status, expires_at, id);

CREATE INDEX ix_approval_run ON approvals (run_id, status, id);


CREATE TABLE audit_logs (
	id BIGINT NOT NULL, 
	workspace_id BIGINT, 
	actor_id BIGINT, 
	session_id BIGINT, 
	run_id BIGINT, 
	action VARCHAR(80), 
	result VARCHAR(80), 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_audit_run ON audit_logs (run_id, id);

CREATE INDEX ix_audit_workspace ON audit_logs (workspace_id, created_at, id);


CREATE TABLE memories (
	id BIGINT NOT NULL, 
	user_id BIGINT, 
	workspace_id BIGINT, 
	session_id BIGINT, 
	kind VARCHAR(30), 
	summary TEXT, 
	content_ref VARCHAR(255), 
	source_record_id BIGINT, 
	version INTEGER, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_memory_scope ON memories (user_id, workspace_id, kind, id);

CREATE INDEX ix_memory_session ON memories (session_id, id);


CREATE TABLE messages (
	id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	session_id BIGINT NOT NULL, 
	run_id BIGINT NOT NULL, 
	seq BIGINT NOT NULL, 
	`role` VARCHAR(20), 
	content_preview TEXT, 
	content_ref VARCHAR(255), 
	content_bytes BIGINT, 
	is_partial BOOL, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_message_seq UNIQUE (session_id, seq)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_message_run ON messages (run_id, id);


CREATE TABLE outbox (
	id BIGINT NOT NULL, 
	run_id BIGINT, 
	published_at DOUBLE, 
	attempts INTEGER NOT NULL, 
	next_attempt_at DOUBLE NOT NULL, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_outbox_pending ON outbox (published_at, next_attempt_at, id);


CREATE TABLE pending_decisions (
	`key` VARCHAR(160) NOT NULL, 
	payload TEXT, 
	expires_at DOUBLE, 
	PRIMARY KEY (`key`)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE run_events (
	run_id BIGINT NOT NULL, 
	event_seq INTEGER NOT NULL, 
	type VARCHAR(40), 
	payload_preview TEXT, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (run_id, event_seq)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE runtime_capacity (
	scope VARCHAR(100) NOT NULL, 
	last_dispatch_at DOUBLE NOT NULL, 
	PRIMARY KEY (scope)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE schema_versions (
	version INTEGER NOT NULL AUTO_INCREMENT, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (version)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE session_records (
	session_id BIGINT NOT NULL, 
	seq BIGINT NOT NULL, 
	record_type VARCHAR(20), 
	record_id BIGINT NOT NULL, 
	PRIMARY KEY (session_id, seq)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE tool_calls (
	id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	session_id BIGINT NOT NULL, 
	run_id BIGINT NOT NULL, 
	seq BIGINT NOT NULL, 
	tool_name VARCHAR(80), 
	args_preview TEXT, 
	args_ref VARCHAR(255), 
	status VARCHAR(24), 
	deadline_at DOUBLE, 
	sandbox_id VARCHAR(100), 
	exit_code INTEGER, 
	term_signal INTEGER, 
	stdout_preview TEXT, 
	stderr_preview TEXT, 
	output_ref VARCHAR(255), 
	output_bytes BIGINT, 
	truncated BOOL, 
	cached BOOL, 
	started_at DOUBLE, 
	finished_at DOUBLE, 
	side_effect_key VARCHAR(100), 
	error_code VARCHAR(80), 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tool_seq UNIQUE (session_id, seq), 
	CONSTRAINT uq_tool_effect UNIQUE (user_id, side_effect_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_tool_run ON tool_calls (run_id, id);


CREATE TABLE users (
	id BIGINT NOT NULL, 
	auth_subject VARCHAR(190) NOT NULL, 
	status VARCHAR(20), 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (auth_subject)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE workspace_members (
	workspace_id BIGINT NOT NULL, 
	user_id BIGINT NOT NULL, 
	`role` VARCHAR(20), 
	PRIMARY KEY (workspace_id, user_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_member_user ON workspace_members (user_id, workspace_id);


CREATE TABLE workspaces (
	id BIGINT NOT NULL, 
	owner_id BIGINT NOT NULL, 
	name VARCHAR(120), 
	snapshot_ref VARCHAR(255), 
	version INTEGER NOT NULL, 
	created_at DOUBLE NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_workspace_owner ON workspaces (owner_id, id);

INSERT INTO runtime_capacity (scope, last_dispatch_at) VALUES ('global', 0);

INSERT INTO schema_versions (version, created_at) VALUES (1, UNIX_TIMESTAMP(NOW(6)));
