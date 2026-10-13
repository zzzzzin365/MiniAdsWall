CREATE TABLE IF NOT EXISTS ads_business_material_archive (
 material_id CHAR(64) NOT NULL PRIMARY KEY,
 archived_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 FOREIGN KEY (material_id) REFERENCES ads_business_materials(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
