CREATE TABLE IF NOT EXISTS marketing_brands (
    id VARCHAR(100) NOT NULL,
    name VARCHAR(500) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY(id),
    UNIQUE KEY uq_brand_environment(id,environment),
    CHECK (version > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_memberships (
    principal VARCHAR(190) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    role ENUM('operator','reviewer','creator') NOT NULL,
    PRIMARY KEY(principal,brand_id,role),
    FOREIGN KEY (brand_id) REFERENCES marketing_brands(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_products (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    name VARCHAR(500) NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY(id),
    UNIQUE KEY uq_product_scope(id,brand_id,environment),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (version > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_ad_owners (
    ad_id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    PRIMARY KEY(ad_id),
    UNIQUE KEY uq_ad_owner_scope(ad_id,brand_id,environment),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_campaigns (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    name VARCHAR(500) NOT NULL,
    objective ENUM('traffic','conversion') NOT NULL,
    status ENUM('draft','active','paused','ended') NOT NULL DEFAULT 'draft',
    starts_at DATETIME(3) NOT NULL,
    ends_at DATETIME(3) NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    created_by VARCHAR(190) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(id),
    UNIQUE KEY uq_campaign_scope(id,brand_id,environment),
    INDEX ix_campaign_scope(brand_id,environment,status,id),
    FOREIGN KEY (product_id,brand_id,environment) REFERENCES marketing_products(id,brand_id,environment),
    CHECK (starts_at < ends_at),
    CHECK (version > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_creatives (
    id VARCHAR(100) NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    ad_id VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    asset_ref VARCHAR(2048) NOT NULL,
    asset_hash CHAR(64) NOT NULL,
    landing_url VARCHAR(2048) NOT NULL,
    status ENUM('draft','pending_review','approved','rejected','archived') NOT NULL DEFAULT 'draft',
    state_revision INT UNSIGNED NOT NULL DEFAULT 1,
    authorization_ref VARCHAR(500) NOT NULL,
    authorization_expires_at DATETIME(3) NOT NULL,
    reviewer VARCHAR(190) NULL,
    reviewed_at DATETIME(3) NULL,
    created_by VARCHAR(190) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(id,version),
    UNIQUE KEY uq_creative_scope(id,version,brand_id,environment,campaign_id),
    INDEX ix_creative_campaign(campaign_id,status,id),
    FOREIGN KEY (campaign_id,brand_id,environment) REFERENCES marketing_campaigns(id,brand_id,environment),
    FOREIGN KEY (ad_id,brand_id,environment) REFERENCES marketing_ad_owners(ad_id,brand_id,environment),
    CHECK (status <> 'approved' OR (reviewer IS NOT NULL AND reviewed_at IS NOT NULL)),
    CHECK (version > 0 AND state_revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_ad_links (
    ad_id VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    current_ad_id VARCHAR(100) GENERATED ALWAYS AS (CASE WHEN is_current THEN ad_id ELSE NULL END) STORED,
    effective_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(ad_id,revision),
    UNIQUE KEY uq_current_ad_link(current_ad_id),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id),
    FOREIGN KEY (ad_id,brand_id,environment) REFERENCES marketing_ad_owners(ad_id,brand_id,environment),
    CHECK (revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_serving_decisions (
    impression_id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    visitor_session_id VARCHAR(100) NOT NULL,
    candidate_ref VARCHAR(2048) NOT NULL,
    policy_version VARCHAR(100) NOT NULL,
    experiment_id VARCHAR(100) NULL,
    experiment_group VARCHAR(100) NULL,
    token_expires_at DATETIME(3) NOT NULL,
    simulated_cpc_minor BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(impression_id),
    UNIQUE KEY uq_impression_environment(impression_id,environment),
    INDEX ix_serving_session(visitor_session_id,created_at),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id),
    CHECK (simulated_cpc_minor IS NULL OR simulated_cpc_minor <= 9007199254740991)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_events (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_event_id VARCHAR(100) NOT NULL,
    type ENUM('impression','click','order_paid','order_refunded') NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    received_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    impression_id VARCHAR(100) NULL,
    click_id VARCHAR(100) NULL,
    order_id VARCHAR(100) NULL,
    product_id VARCHAR(100) NULL,
    amount_minor BIGINT UNSIGNED NULL,
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    schema_version INT UNSIGNED NOT NULL DEFAULT 1,
    payload_hash CHAR(64) NOT NULL,
    payload JSON NOT NULL,
    validation_status ENUM('valid','quarantined','pending') NOT NULL DEFAULT 'valid',
    isolation_reason VARCHAR(100) NULL,
    valid_impression VARCHAR(100) GENERATED ALWAYS AS (CASE WHEN type='impression' AND validation_status='valid' THEN impression_id ELSE NULL END) STORED,
    valid_click_impression VARCHAR(100) GENERATED ALWAYS AS (CASE WHEN type='click' AND validation_status='valid' THEN impression_id ELSE NULL END) STORED,
    paid_order VARCHAR(100) GENERATED ALWAYS AS (CASE WHEN type='order_paid' AND validation_status='valid' THEN order_id ELSE NULL END) STORED,
    PRIMARY KEY(id),
    UNIQUE KEY uq_source_event(source,source_event_id),
    UNIQUE KEY uq_valid_impression(environment,valid_impression),
    UNIQUE KEY uq_valid_click(environment,valid_click_impression),
    UNIQUE KEY uq_paid_order(source,environment,paid_order),
    INDEX ix_event_time(environment,type,occurred_at,id),
    INDEX ix_event_received(received_at,id),
    INDEX ix_event_order(source,order_id,received_at),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (currency='CNY'),
    CHECK (schema_version=1),
    CHECK (amount_minor IS NULL OR amount_minor <= 9007199254740991),
    CHECK (validation_status<>'valid' OR type NOT IN ('impression','click') OR impression_id IS NOT NULL),
    CHECK (validation_status<>'valid' OR type<>'click' OR click_id IS NOT NULL),
    CHECK (validation_status<>'valid' OR type NOT IN ('order_paid','order_refunded') OR (order_id IS NOT NULL AND product_id IS NOT NULL AND amount_minor IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_order_attributions (
    source VARCHAR(100) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    click_id VARCHAR(100) NULL,
    rule_version VARCHAR(100) NOT NULL,
    net_revenue_minor BIGINT UNSIGNED NOT NULL,
    status ENUM('attributed','unattributed','pending') NOT NULL,
    evidence JSON NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(source,order_id,environment,revision),
    INDEX ix_attribution_click(click_id,revision),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (revision > 0),
    CHECK (net_revenue_minor <= 9007199254740991)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_order_attribution_heads (
    source VARCHAR(100) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    revision INT UNSIGNED NOT NULL,
    PRIMARY KEY(source,order_id,environment),
    FOREIGN KEY (source,order_id,environment,revision) REFERENCES marketing_order_attributions(source,order_id,environment,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_spend_ledger (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_entry_id VARCHAR(100) NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    amount_minor BIGINT NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    adjustment_ref VARCHAR(100) NULL,
    payload_hash CHAR(64) NOT NULL,
    PRIMARY KEY(id),
    UNIQUE KEY uq_spend_source(source,source_entry_id),
    INDEX ix_spend_campaign(campaign_id,occurred_at,id),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id),
    CHECK (currency='CNY'),
    CHECK (amount_minor BETWEEN -9007199254740991 AND 9007199254740991)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_source_coverage (
    campaign_id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    source VARCHAR(100) NOT NULL,
    starts_at DATETIME(3) NOT NULL,
    ends_at DATETIME(3) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    expected_count BIGINT UNSIGNED NOT NULL,
    expected_amount_minor BIGINT UNSIGNED NOT NULL,
    checksum CHAR(64) NOT NULL,
    status ENUM('complete','incomplete','invalidated') NOT NULL,
    reason VARCHAR(100) NULL,
    PRIMARY KEY(campaign_id,environment,source,starts_at,ends_at,revision),
    FOREIGN KEY (campaign_id,brand_id,environment) REFERENCES marketing_campaigns(id,brand_id,environment),
    CHECK (starts_at < ends_at),
    CHECK (revision > 0),
    CHECK (expected_count <= 9007199254740991 AND expected_amount_minor <= 9007199254740991)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_daily_metrics (
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    campaign_id VARCHAR(100) NOT NULL,
    creative_id VARCHAR(100) NOT NULL,
    creative_version INT UNSIGNED NOT NULL,
    day DATE NOT NULL,
    rule_version VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    impressions BIGINT UNSIGNED NOT NULL DEFAULT 0,
    clicks BIGINT UNSIGNED NOT NULL DEFAULT 0,
    paid_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
    refunded_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
    revenue_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
    spend_minor BIGINT NOT NULL DEFAULT 0,
    received_cutoff DATETIME(3) NOT NULL,
    PRIMARY KEY(environment,campaign_id,creative_id,creative_version,day,rule_version,revision),
    FOREIGN KEY (creative_id,creative_version,brand_id,environment,campaign_id) REFERENCES marketing_creatives(id,version,brand_id,environment,campaign_id),
    CHECK (revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_outbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    kind VARCHAR(100) NOT NULL,
    aggregate_id VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL DEFAULT 1,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    payload JSON NOT NULL,
    status ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending',
    lease_until DATETIME(3) NULL,
    lease_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
    attempt INT UNSIGNED NOT NULL DEFAULT 0,
    next_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    error_code VARCHAR(100) NULL,
    PRIMARY KEY(id),
    UNIQUE KEY uq_marketing_outbox(kind,aggregate_id,revision),
    INDEX ix_marketing_outbox(status,next_at,id),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_report_snapshots (
    id VARCHAR(100) NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    created_by VARCHAR(190) NOT NULL,
    query JSON NOT NULL,
    query_hash CHAR(64) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    received_cutoff DATETIME(3) NOT NULL,
    payload_ref VARCHAR(2048) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(id),
    FOREIGN KEY (brand_id,environment) REFERENCES marketing_brands(id,environment),
    CHECK (revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
