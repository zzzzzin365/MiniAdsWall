-- Additive only. NULL and 1 both identify legacy persisted input/execution records.
ALTER TABLE agent_runs ADD COLUMN input_protocol_version INTEGER NULL DEFAULT 1;
ALTER TABLE tool_calls ADD COLUMN executor_protocol_version INTEGER NULL DEFAULT 1;
