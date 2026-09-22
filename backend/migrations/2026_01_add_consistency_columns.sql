-- 一致性优化：asset_items 表新增字段（方案 3 场景几何锚定 + 正脸定妆图）
-- 执行环境：PostgreSQL（数据库 aigc_workbench）
-- PostgreSQL 的列顺序不可指定（无 MySQL 的 AFTER），新增列一律排在表尾。

ALTER TABLE asset_items
    ADD COLUMN IF NOT EXISTS spatial_layout TEXT NULL,
    ADD COLUMN IF NOT EXISTS portrait_prompt TEXT NULL,
    ADD COLUMN IF NOT EXISTS portrait_path VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS portrait_url VARCHAR(1000) NULL;

COMMENT ON COLUMN asset_items.spatial_layout IS '场景空间布局（机位/标志物方位/光源，用于跨镜头场景一致性）';
COMMENT ON COLUMN asset_items.portrait_prompt IS '角色正脸定妆图 prompt（干净背景半身定妆）';
COMMENT ON COLUMN asset_items.portrait_path IS '角色正脸定妆图本地路径';
COMMENT ON COLUMN asset_items.portrait_url IS '角色正脸定妆图远程 URL';
