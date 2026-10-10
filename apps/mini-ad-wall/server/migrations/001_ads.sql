CREATE TABLE IF NOT EXISTS ads_business_ads (
    id VARCHAR(100) NOT NULL PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    publisher VARCHAR(255) NOT NULL,
    content LONGTEXT NOT NULL,
    url TEXT NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    clicks BIGINT UNSIGNED NOT NULL DEFAULT 0,
    videos JSON NOT NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    ranking_score DECIMAL(38,8) GENERATED ALWAYS AS (price + price * clicks * 0.42) STORED,
    INDEX ix_ads_ranking (ranking_score DESC, id ASC),
    CONSTRAINT ck_ads_price CHECK (price > 0),
    CONSTRAINT ck_ads_clicks CHECK (clicks <= 9007199254740991),
    CONSTRAINT ck_ads_version CHECK (version > 0),
    CONSTRAINT ck_ads_videos CHECK (JSON_TYPE(videos) = 'ARRAY')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ads_business_operations (
    owner VARCHAR(190) NOT NULL,
    operation_key VARCHAR(100) NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    status SMALLINT UNSIGNED NULL,
    body JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (owner, operation_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ads_business_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner VARCHAR(190) NOT NULL,
    operation_key VARCHAR(100) NOT NULL,
    action VARCHAR(160) NOT NULL,
    before_data JSON NULL,
    after_data JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX ix_ads_audit_operation (owner, operation_key, id),
    FOREIGN KEY (owner, operation_key) REFERENCES ads_business_operations (owner, operation_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS ads_business_migrations (
    name VARCHAR(100) NOT NULL PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
