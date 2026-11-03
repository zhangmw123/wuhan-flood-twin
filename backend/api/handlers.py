"""API 业务处理层：所有路由的纯函数实现，供 FastAPI 与零依赖 dev server 共用。"""
import json
import os
import time

from ..core import geo
from ..core.config import PROCESSED_DIR, TILE_DIR, settings
from ..core.store import get_store
from ..core.tilecache import TILE_CACHE, load_tile
from ..services import analysis, copilot, report, weather


# ------------------------------------------------------------------ 态势


def get_meta():
    store = get_store()
    return {
        "code": 200,
        "data": {
            "city": "武汉市",
            "center": list(settings.CITY_CENTER),
            "bbox": list(settings.CITY_BBOX),
            "engine": settings.SPATIAL_ENGINE,
            "tile_source": settings.TILE_SOURCE,
            "dataset": store.meta,
            "status": store.station_meta(),
            "warning_level_m": settings.HANKOU_WARNING_LEVEL,
            "guarantee_level_m": settings.HANKOU_GUARANTEE_LEVEL,
            "weather": weather.weather_status(),
            "llm_enabled": bool(settings.LLM_API_KEY),
            "load_seconds": store.load_seconds,
        },
    }


def get_stations(mode=None, district=None):
    store = get_store()
    if mode and mode != store.mode:
        store.set_mode(mode)
    fc = store.station_payload()
    if district:
        fc["features"] = [f for f in fc["features"]
                          if f["properties"].get("district") == district]
    return {
        "type": "FeatureCollection",
        "metadata": store.station_meta(),
        "features": fc["features"],
    }


def get_control():
    return {"code": 200, "data": get_store().station_meta()}


def post_control(body):
    store = get_store()
    if body.get("mode"):
        store.set_mode(body["mode"])
    if body.get("hour") is not None:
        store.set_hour(float(body["hour"]))
    if body.get("playing") is not None:
        store.playing = bool(body["playing"])
        store.last_tick = time.time()
    return {"code": 200, "data": store.station_meta()}


def get_districts_stats():
    return {"code": 200, "data": analysis.district_stats()}


def get_water_level():
    store = get_store()
    return {
        "code": 200,
        "data": {
            "series": analysis.water_level_series(),
            "current_hour": round(store.hour, 2),
            "warning_level_m": settings.HANKOU_WARNING_LEVEL,
            "guarantee_level_m": settings.HANKOU_GUARANTEE_LEVEL,
            "rain_hourly": [round(store.rain_at(h), 1) for h in range(24)],
        },
    }


def get_alerts():
    store = get_store()
    return {"code": 200, "data": {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "mode": store.mode, "hour": round(store.hour, 2),
        "items": store.alerts(),
    }}


# ------------------------------------------------------------------ 图层


def _read_geojson(name):
    with open(os.path.join(PROCESSED_DIR, name), "r", encoding="utf-8") as fp:
        return json.load(fp)


def get_layer(kind, bbox=None, limit=20000):
    store = get_store()
    if kind == "districts":
        return _read_geojson("wuhan_districts.geojson")
    if kind == "water":
        return _read_geojson("wuhan_water.geojson")
    if kind == "shelters":
        return _read_geojson("wuhan_shelters.geojson")
    if kind == "poi":
        return _read_geojson("wuhan_key_poi.geojson")
    if kind == "stations":
        return store.station_payload()
    if kind in ("buildings", "roads"):
        if not bbox:
            bbox = settings.CITY_BBOX
        return _merge_tiles(kind, bbox, limit)
    raise ValueError(f"unknown layer: {kind}")


def _merge_tiles(kind, bbox, limit):
    """按视域合并 z12 瓦片。

    优化点（对比首版）：
      1. 瓦片经 LRU 缓存，重复请求不再重复 json.load（单瓦片 7MB / 2.6 万栋）
      2. 缓存时预计算要素外包框，命中后只做 bbox 相交判断
      3. 用瓦片外包框先做粗筛，完全不相交的瓦片直接跳过解析
    """
    z = 12
    feats = []
    seen = set()
    for (_z, x, y) in geo.tiles_for_bbox(bbox, z):
        # 粗筛：瓦片整体不在视域内则无需解析（省掉一次 7MB JSON 解析）
        tile_box = geo.tile_bbox(z, x, y)
        if not geo.bbox_intersects(tile_box, bbox):
            continue
        for f, fb in load_tile(TILE_DIR, kind, z, x, y):
            if not geo.bbox_intersects(fb, bbox):
                continue
            fid = f["properties"].get("id")
            if fid in seen:
                continue
            seen.add(fid)
            feats.append(f)
            if len(feats) >= limit:
                break
        if len(feats) >= limit:
            break
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"count": len(feats), "zoom": z, "truncated": len(feats) >= limit}}


def get_tile(kind, z, x, y):
    path = os.path.join(TILE_DIR, kind, str(z), f"{x}_{y}.json")
    if not os.path.exists(path):
        return {"type": "FeatureCollection", "features": []}
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


# ------------------------------------------------------------------ 分析


def post_impact(body):
    rainfall = float(body.get("rainfall_mm", 120))
    duration = float(body.get("duration_hours", 3))
    district = body.get("target_district") or None
    use_state = bool(body.get("use_state", False))
    return analysis.impact_analysis(rainfall, duration, district, use_state)


def get_briefing(rainfall_mm=120.0, duration_h=3.0, district=None):
    """防汛会商简报（Markdown 正文 + 摘要 + 建议文件名）。"""
    r = report.build_briefing(float(rainfall_mm), float(duration_h), district or None)
    return {"code": 200, "data": r}


def post_evacuation(body):
    lng = float(body["origin_lng"])
    lat = float(body["origin_lat"])
    return analysis.evacuation_route(lng, lat, bool(body.get("avoid_waterlogging", True)))


def get_buffer(lng, lat, radius_m=500):
    return {"code": 200, "data": analysis.buffer_analysis(float(lng), float(lat), float(radius_m))}


def post_copilot(body):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return {"code": 400, "message": "prompt 不能为空"}
    result = copilot.chat(prompt)
    result["code"] = 200
    return result


def get_weather():
    rain, source = weather.fetch_district_rain()
    return {"code": 200, "data": {
        "source": source,
        "districts": rain,
        "hourly": weather.get_hourly_series(),
        "url": weather.weather_url_preview(),
    }}


def get_health():
    store = get_store()
    return {"status": "ok", "engine": settings.SPATIAL_ENGINE,
            "buildings": len(store.buildings), "roads": len(store.roads),
            "load_seconds": store.load_seconds,
            "tile_cache": TILE_CACHE.stats()}
