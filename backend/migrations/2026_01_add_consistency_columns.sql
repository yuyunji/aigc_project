-- 一致性优化：asset_items 表新增字段（方案 3 场景几何锚定 + 正脸定妆图）
-- 执行环境：MySQL（数据库 aigc_workbench）

ALTER TABLE asset_items
    ADD COLUMN spatial_layout TEXT NULL COMMENT '场景空间布局（机位/标志物方位/光源，用于跨镜头场景一致性）' AFTER image_prompt,
    ADD COLUMN portrait_prompt TEXT NULL COMMENT '角色正脸定妆图 prompt（干净背景半身定妆）' AFTER spatial_layout,
    ADD COLUMN portrait_path VARCHAR(500) NULL COMMENT '角色正脸定妆图本地路径' AFTER portrait_prompt,
    ADD COLUMN portrait_url VARCHAR(1000) NULL COMMENT '角色正脸定妆图远程 URL' AFTER portrait_path;
