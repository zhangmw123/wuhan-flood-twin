#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""02_import_postgis.py —— 将预处理 GeoJSON/CSV 经 ogr2ogr 或 geopandas 导入 PostGIS。

用法（需安装 geopandas + psycopg2 / sqlalchemy）：
    python scripts/02_import_postgis.py \
        --dsn postgresql://postgres:postgres123@localhost:5432/wuhan_flood_gis

也可用 GDAL 一条命令导入大表（推荐 100MB 级数据）：
    ogr2ogr -f PostgreSQL PG:"postgresql://postgres:postgres123@localhost:5432/wuhan_flood_gis" \
        data/tiles/buildings_src.geojson -nln wuhan_buildings -nlt MULTIPOLYGON -lco GEOMETRY_NAME=geom
"""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED = os.path.join(ROOT, "data", "processed")


def import_with_geopandas(dsn):
    import geopandas as gpd
    from sqlalchemy import create_engine, text

    engine = create_engine(dsn)

    jobs = [
        ("wuhan_districts.geojson", "wuhan_districts", {"name": "name", "adcode": "adcode"}),
        ("wuhan_water.geojson", "wuhan_water", {"name": "name", "water_type": "water_type", "data_source": "data_source"}),
        ("wuhan_shelters.geojson", "wuhan_shelters", {"name": "name", "address": "address", "district": "district", "capacity": "capacity", "contact_phone": "contact_phone", "shelter_type": "shelter_type"}),
        ("wuhan_key_poi.geojson", "wuhan_key_poi", {"name": "name", "district": "district", "address": "address", "poi_type": "poi_type"}),
        ("wuhan_waterlogging.geojson", "wuhan_waterlogging_points", None),
    ]
    for src, table, cols in jobs:
        gdf = gpd.read_file(os.path.join(PROCESSED, src))
        if cols:
            keep = list(cols.keys()) + ["geometry"]
            gdf = gdf[keep].rename(columns=cols)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {table}"))
        gdf.to_postgis(table, engine, if_exists="append", index=False, chunksize=5000)
        print(f"✅ {table}: {len(gdf)} 行")

    # 建筑：从分析索引 CSV 构建中心点表（多边形建议用 ogr2ogr 导入完整轮廓）
    with open(os.path.join(PROCESSED, "buildings.csv"), encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    gdf = gpd.GeoDataFrame(
        [{"id": int(r["id"]), "elevation": float(r["elevation"]), "district": r["district"],
          "geometry": None} for r in rows],
        geometry=None,
    )
    print(f"ℹ️  建筑 {len(rows)} 条：请用 ogr2ogr 导入完整多边形（见文件头注释），"
          f"MVT 服务由 Martin 自动扫描 wuhan_buildings 表发布。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://postgres:postgres123@localhost:5432/wuhan_flood_gis")
    args = ap.parse_args()
    try:
        import geopandas  # noqa: F401
    except ImportError:
        sys.exit("请先安装: pip install geopandas psycopg2-binary sqlalchemy geoalchemy2")
    import_with_geopandas(args.dsn)


if __name__ == "__main__":
    main()
