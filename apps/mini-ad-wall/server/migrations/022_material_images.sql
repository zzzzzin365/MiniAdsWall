CREATE TABLE IF NOT EXISTS ads_business_material_images (
 material_id CHAR(64) NOT NULL PRIMARY KEY,
 images JSON NOT NULL,
 FOREIGN KEY (material_id) REFERENCES ads_business_materials(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
