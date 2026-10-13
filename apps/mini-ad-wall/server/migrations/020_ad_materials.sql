CREATE TABLE IF NOT EXISTS ads_business_materials (
 id CHAR(64) NOT NULL PRIMARY KEY,
 name VARCHAR(500) NOT NULL,
 content LONGTEXT NOT NULL,
 videos JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE IF NOT EXISTS ads_business_material_links (
 ad_id VARCHAR(100) NOT NULL PRIMARY KEY,
 material_id CHAR(64) NOT NULL,
 INDEX ix_material_usage (material_id),
 FOREIGN KEY (ad_id) REFERENCES ads_business_ads(id) ON DELETE CASCADE,
 FOREIGN KEY (material_id) REFERENCES ads_business_materials(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
INSERT IGNORE INTO ads_business_materials(id,name,content,videos)
 SELECT SHA2(CONCAT(content,CHAR(0),CAST(videos AS CHAR)),256),MIN(title),content,videos
 FROM ads_business_ads GROUP BY content,videos;
INSERT IGNORE INTO ads_business_material_links(ad_id,material_id)
 SELECT id,SHA2(CONCAT(content,CHAR(0),CAST(videos AS CHAR)),256) FROM ads_business_ads;
