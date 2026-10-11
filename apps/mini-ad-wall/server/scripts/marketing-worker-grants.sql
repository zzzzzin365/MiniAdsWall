-- Run as a DBA after creating 'marketing_worker'@'localhost' with a secret
-- managed outside this file. Replace miniadswall with the deployment DB name.
-- A separate operator DB account schedules rebuild requests; workers cannot
-- insert outbox tasks, change raw facts, or read/write legacy ads.
GRANT SELECT ON miniadswall.marketing_schema_migrations TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_brands TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_campaigns TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_serving_decisions TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_events TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_event_validation TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_spend_ledger TO 'marketing_worker'@'localhost';
GRANT SELECT ON miniadswall.marketing_spend_isolation TO 'marketing_worker'@'localhost';
GRANT SELECT, UPDATE ON miniadswall.marketing_outbox TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT ON miniadswall.marketing_input_epochs TO 'marketing_worker'@'localhost';
-- MySQL requires a write privilege for SELECT ... FOR UPDATE on this clock.
GRANT UPDATE(epoch) ON miniadswall.marketing_input_epochs TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT, UPDATE ON miniadswall.marketing_aggregation_state TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT ON miniadswall.marketing_order_attributions TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT, UPDATE ON miniadswall.marketing_order_attribution_heads TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT ON miniadswall.marketing_daily_metrics TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT, UPDATE ON miniadswall.marketing_metric_heads TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON miniadswall.marketing_click_orders TO 'marketing_worker'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON miniadswall.marketing_pending_partitions TO 'marketing_worker'@'localhost';
GRANT SELECT, UPDATE ON miniadswall.marketing_rebuild_jobs TO 'marketing_worker'@'localhost';
