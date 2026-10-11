CREATE TABLE IF NOT EXISTS marketing_input_epochs (
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY(brand_id,environment),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_aggregation_state (
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    generation BIGINT UNSIGNED NOT NULL DEFAULT 0,
    input_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
    received_cutoff DATETIME(3) NULL,
    PRIMARY KEY(brand_id,environment,rule_version),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_metric_heads (
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    day DATE NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    input_epoch BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY(environment,campaign_id,creative_id,creative_version,day,rule_version),
    INDEX ix_metric_head_scope(brand_id,environment,day,rule_version),
    FOREIGN KEY (environment,campaign_id,creative_id,creative_version,day,rule_version,revision) REFERENCES marketing_daily_metrics(environment,campaign_id,creative_id,creative_version,day,rule_version,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_click_orders (
    source VARCHAR(100) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    attribution_revision INT UNSIGNED NOT NULL,
    click_id VARCHAR(100) NOT NULL,
    click_at DATETIME(3) NOT NULL,
    paid_at DATETIME(3) NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    net_revenue_minor BIGINT UNSIGNED NOT NULL,
    refunded_minor BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY(source,order_id,environment,rule_version),
    INDEX ix_click_order_cohort(environment,campaign_id,click_at,rule_version),
    INDEX ix_click_order_click(environment,click_id,rule_version),
    FOREIGN KEY (source,order_id,environment,attribution_revision) REFERENCES marketing_order_attributions(source,order_id,environment,revision),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_rebuild_jobs (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    starts_on DATE NOT NULL,
    ends_before DATE NOT NULL,
    requested_by VARCHAR(190) NOT NULL,
    status ENUM('pending','done','failed') NOT NULL DEFAULT 'pending',
    result JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at DATETIME(3) NULL,
    PRIMARY KEY(id),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (starts_on < ends_before)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_pending_partitions (
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    day DATE NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    input_epoch BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(100) NOT NULL,
    PRIMARY KEY(environment,campaign_id,creative_id,creative_version,day,rule_version),
    INDEX ix_pending_partition_scope(brand_id,environment,day),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
