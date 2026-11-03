-- =====================================================================
-- 江汉流域防汛抗旱与武汉城市内涝二三维孪生调度平台
-- PostGIS 空间数据库初始化脚本（docker-compose 自动执行）
-- 所有空间表统一 EPSG:4326 存储，米制计算时 ST_Transform(3857)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. 武汉市建筑轮廓（206,270 栋，含 Elevation 楼高）
CREATE TABLE IF NOT EXISTS wuhan_buildings (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(128),
    elevation   DOUBLE PRECISION DEFAULT 15.0,
    ground_dem  DOUBLE PRECISION DEFAULT 21.0,
    district    VARCHAR(64),
    geom        GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_buildings_geom ON wuhan_buildings USING GIST (geom);

-- 2. 武汉市市政道路网（29,267 条，OSM）
CREATE TABLE IF NOT EXISTS wuhan_roads (
    id         SERIAL PRIMARY KEY,
    osm_id     BIGINT,
    name       VARCHAR(128),
    road_class VARCHAR(32) DEFAULT '主干道',
    length_m   DOUBLE PRECISION,
    geom       GEOMETRY(MultiLineString, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_roads_geom ON wuhan_roads USING GIST (geom);

-- 3. 应急避难场所（195 处：学校操场/体育馆 + 公开场馆）
CREATE TABLE IF NOT EXISTS wuhan_shelters (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(128) NOT NULL,
    address       VARCHAR(256),
    district      VARCHAR(64),
    capacity      INTEGER DEFAULT 2000,
    contact_phone VARCHAR(32),
    shelter_type  VARCHAR(64),
    geom          GEOMETRY(Point, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_shelters_geom ON wuhan_shelters USING GIST (geom);

-- 4. 行政区划（13 区）与水系
CREATE TABLE IF NOT EXISTS wuhan_districts (
    id     SERIAL PRIMARY KEY,
    adcode VARCHAR(16),
    name   VARCHAR(64) NOT NULL,
    geom   GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_districts_geom ON wuhan_districts USING GIST (geom);

CREATE TABLE IF NOT EXISTS wuhan_water (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(128),
    water_type   VARCHAR(32),          -- 'river' / 'lake'
    normal_level DOUBLE PRECISION DEFAULT 20.0,
    data_source  VARCHAR(64),
    geom         GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_water_geom ON wuhan_water USING GIST (geom);

-- 5. 渍水点 / 水文站 / 排涝泵站
CREATE TABLE IF NOT EXISTS wuhan_waterlogging_points (
    id                  SERIAL PRIMARY KEY,
    station_code        VARCHAR(32) UNIQUE NOT NULL,
    name                VARCHAR(128) NOT NULL,
    district            VARCHAR(64) NOT NULL,
    station_type        VARCHAR(32) NOT NULL,   -- waterlogging / hydrology / pump
    warning_threshold_cm DOUBLE PRECISION DEFAULT 30.0,
    danger_threshold_cm  DOUBLE PRECISION DEFAULT 50.0,
    warning_level_m     DOUBLE PRECISION,
    guarantee_level_m   DOUBLE PRECISION,
    capacity_m3s        DOUBLE PRECISION,
    pump_name           VARCHAR(128),
    current_depth_cm    DOUBLE PRECISION DEFAULT 0.0,
    rain_1h_mm          DOUBLE PRECISION DEFAULT 0.0,
    alert_level         INTEGER DEFAULT 0,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    geom                GEOMETRY(Point, 4326)
);
CREATE INDEX IF NOT EXISTS idx_waterlogging_geom ON wuhan_waterlogging_points USING GIST (geom);

-- 6. 重点防护单位（高校 / 医院 / 景点，376 处）
CREATE TABLE IF NOT EXISTS wuhan_key_poi (
    id       SERIAL PRIMARY KEY,
    name     VARCHAR(128),
    district VARCHAR(64),
    address  VARCHAR(256),
    poi_type VARCHAR(32),
    geom     GEOMETRY(Point, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wuhan_key_poi_geom ON wuhan_key_poi USING GIST (geom);

-- 7. 实时降雨时序（Open-Meteo 采集落库）
CREATE TABLE IF NOT EXISTS sensor_readings (
    id          BIGSERIAL PRIMARY KEY,
    district    VARCHAR(64) NOT NULL,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rain_1h_mm  DOUBLE PRECISION,
    temp_c      DOUBLE PRECISION,
    source      VARCHAR(32) DEFAULT 'open-meteo'
);
CREATE INDEX IF NOT EXISTS idx_sensor_readings ON sensor_readings (district, recorded_at DESC);

-- =====================================================================
-- 核心空间分析 SQL（与 local 几何引擎同口径，供后端 postgis 模式调用）
-- =====================================================================

-- 内涝淹没叠加评估：渍水点按积水深度外扩，统计受淹建筑与阻断道路
-- CREATE OR REPLACE FUNCTION fn_inundation_impact(p_rainfall_mm NUMERIC, p_hours NUMERIC, p_district VARCHAR)
-- RETURNS TABLE (...) AS $$
-- WITH inundated_zones AS (
--     SELECT id, name, district,
--            ST_Transform(ST_Buffer(ST_Transform(geom, 3857),
--                        LEAST(current_depth_cm * 8.5, 900)), 4326) AS flood_poly
--     FROM wuhan_waterlogging_points
--     WHERE station_type = 'waterlogging'
--       AND current_depth_cm >= 15
--       AND (p_district IS NULL OR district = p_district)
-- )
-- SELECT COUNT(DISTINCT b.id)                        AS affected_buildings,
--        ROUND(SUM(ST_Area(ST_Transform(z.flood_poly, 3857))) / 1e6, 2) AS area_km2,
--        COUNT(DISTINCT r.id)                        AS blocked_roads,
--        ROUND(SUM(ST_Length(ST_Intersection(r.geom, z.flood_poly)::geography)) / 1000, 2) AS blocked_km
-- FROM inundated_zones z
-- LEFT JOIN wuhan_buildings b ON ST_Intersects(z.flood_poly, b.geom)
-- LEFT JOIN wuhan_roads     r ON ST_Intersects(z.flood_poly, r.geom)
-- GROUP BY z.id;
-- $$ LANGUAGE SQL;
