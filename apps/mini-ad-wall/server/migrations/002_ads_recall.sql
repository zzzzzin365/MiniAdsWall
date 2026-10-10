CREATE TABLE IF NOT EXISTS ads_business_recall_fields (
 field_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 field_key VARBINARY(64) NOT NULL UNIQUE,
 cardinality ENUM('scalar','multi') NOT NULL,
 max_values INT UNSIGNED NOT NULL DEFAULT 50,
 schema_version INT UNSIGNED NOT NULL DEFAULT 1
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_recall_terms (
 term_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 field_id INT UNSIGNED NOT NULL,
 value VARBINARY(256) NOT NULL,
 UNIQUE KEY ix_term_value(field_id,value),
 UNIQUE KEY ix_term_field(field_id,term_id),
 FOREIGN KEY (field_id) REFERENCES ads_business_recall_fields(field_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_recall_docs (
 doc_id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 ad_id VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL UNIQUE,
 shard_id TINYINT UNSIGNED NOT NULL DEFAULT 0,
 index_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 deleted BOOLEAN NOT NULL DEFAULT FALSE,
 starts_at DATETIME(3) NULL,
 ends_at DATETIME(3) NULL,
 INDEX ix_recall_docs(shard_id,doc_id),
 CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_ad_attributes (
 doc_id INT UNSIGNED NOT NULL,
 field_id INT UNSIGNED NOT NULL,
 term_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY(doc_id,field_id,term_id),
 INDEX ix_attribute_term(field_id,term_id,doc_id),
 FOREIGN KEY (doc_id) REFERENCES ads_business_recall_docs(doc_id),
 FOREIGN KEY (field_id,term_id) REFERENCES ads_business_recall_terms(field_id,term_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_recall_partition_state (
 shard_id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
 committed_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
 retained_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_recall_outbox (
 shard_id TINYINT UNSIGNED NOT NULL,
 seq BIGINT UNSIGNED NOT NULL,
 doc_id INT UNSIGNED NOT NULL,
 index_revision BIGINT UNSIGNED NOT NULL,
 event_type ENUM('upsert','delete') NOT NULL,
 schema_version INT UNSIGNED NOT NULL DEFAULT 1,
 payload JSON NOT NULL,
 payload_bytes INT UNSIGNED NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 PRIMARY KEY(shard_id,seq),
 UNIQUE KEY ix_document_revision(doc_id,index_revision),
 INDEX ix_recall_event_age(created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ads_business_recall_consumers (
 consumer_id VARCHAR(190) NOT NULL PRIMARY KEY,
 generation_id VARCHAR(100) NOT NULL,
 lease_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
 lease_until DATETIME(3) NOT NULL,
 durable_manifest_ref TEXT NULL
) ENGINE=InnoDB;
