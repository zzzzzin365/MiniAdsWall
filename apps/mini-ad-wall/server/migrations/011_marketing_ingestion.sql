CREATE TABLE IF NOT EXISTS marketing_ingestion_receipts (
    source VARCHAR(100) NOT NULL,
    source_event_id VARCHAR(100) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    response_status INT NULL,
    response_body JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(source,source_event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_click_links (
    impression_id VARCHAR(100) NOT NULL,
    click_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    visitor_session_id VARCHAR(100) NOT NULL,
    click_token VARCHAR(4096) NOT NULL,
    redirect_url TEXT NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    PRIMARY KEY(impression_id),
    UNIQUE KEY uq_marketing_click_id(click_id),
    FOREIGN KEY (impression_id,environment) REFERENCES marketing_serving_decisions(impression_id,environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_source_policies (
    campaign_id VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    PRIMARY KEY(campaign_id,source,environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_order_ingestion (
    source VARCHAR(100) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    environment ENUM('synthetic','sandbox','production') NOT NULL,
    brand_id VARCHAR(100) NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    paid_event_id VARCHAR(100) NULL,
    paid_amount_minor BIGINT UNSIGNED NULL,
    refunded_minor BIGINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY(source,order_id,environment),
    FOREIGN KEY (product_id,brand_id,environment) REFERENCES marketing_products(id,brand_id,environment),
    CHECK (paid_amount_minor IS NULL OR paid_amount_minor <= 9007199254740991),
    CHECK (paid_amount_minor IS NULL OR refunded_minor <= paid_amount_minor)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_event_validation (
    event_id VARCHAR(100) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    status ENUM('valid','pending','quarantined') NOT NULL,
    reason VARCHAR(100) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(event_id,revision),
    FOREIGN KEY (event_id) REFERENCES marketing_events(id),
    CHECK (revision > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS marketing_spend_isolation (
    id VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_entry_id VARCHAR(100) NOT NULL,
    payload JSON NOT NULL,
    reason VARCHAR(100) NOT NULL,
    received_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(id),
    UNIQUE KEY uq_spend_isolation(source,source_entry_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
