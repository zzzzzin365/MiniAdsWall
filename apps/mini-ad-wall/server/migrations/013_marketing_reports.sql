CREATE TABLE IF NOT EXISTS marketing_report_payloads (
    report_id VARCHAR(100) NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY(report_id),
    FOREIGN KEY (report_id) REFERENCES marketing_report_snapshots(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
