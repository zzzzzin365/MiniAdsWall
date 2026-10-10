-- Legacy operation fingerprints remain unchanged; future protocols need an explicit version.
ALTER TABLE ads_business_operations ADD COLUMN request_protocol_version INT NULL DEFAULT 1;
