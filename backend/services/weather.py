"""实时气象采集：Open-Meteo 免 Key 接口，按 13 个行政区中心点批量拉取小时降水。"""
import json
import time
import urllib.parse
import urllib.request

from ..core.config import settings
from ..core.store import get_store

_cache = {"ts": 0.0, "rain": {}, "hourly": {}, "source": "init"}


def _http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "wuhan-flood-twin/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_district_rain(force=False):
    """返回 {district: 当前小时雨强 mm/h}；失败时返回全 0 并标记 source=offline。"""
    now = time.time()
    if not force and now - _cache["ts"] < settings.WEATHER_TTL_SEC and _cache["rain"]:
        return _cache["rain"], _cache["source"]

    store = get_store()
    names = list(store.district_center.keys())
    lats = ",".join(f"{store.district_center[n][1]:.4f}" for n in names)
    lons = ",".join(f"{store.district_center[n][0]:.4f}" for n in names)
    url = (f"{settings.OPEN_METEO_URL}?latitude={lats}&longitude={lons}"
           "&current=precipitation,rain&hourly=precipitation&forecast_days=2&timezone=Asia%2FShanghai")
    try:
        data = _http_get(url)
        items = data if isinstance(data, list) else [data]
        rain, hourly = {}, {}
        for name, item in zip(names, items):
            cur = item.get("current", {}) or {}
            v = cur.get("precipitation")
            if v is None:
                v = cur.get("rain", 0) or 0
            rain[name] = float(v)
            h = (item.get("hourly", {}) or {}).get("precipitation", [])[:24]
            hourly[name] = [float(x or 0) for x in h]
        _cache.update(ts=now, rain=rain, hourly=hourly, source="open-meteo")
        return rain, "open-meteo"
    except Exception as exc:  # 网络不可用时降级，保证大屏仍可演示
        rain = {n: 0.0 for n in names}
        _cache.update(ts=now, rain=rain, hourly={}, source=f"offline: {type(exc).__name__}")
        return rain, _cache["source"]


def get_realtime_rain():
    rain, _ = fetch_district_rain()
    return rain


def get_hourly_series(district=None):
    """13 区未来 24h 降水预报，用于前端雨强曲线。"""
    fetch_district_rain()
    hourly = _cache.get("hourly") or {}
    if district and district in hourly:
        return hourly[district]
    if not hourly:
        return []
    n = min(len(v) for v in hourly.values())
    return [round(sum(hourly[d][i] for d in hourly) / len(hourly), 2) for i in range(n)]


def weather_status():
    _, source = fetch_district_rain()
    return {"source": source, "ttl_sec": settings.WEATHER_TTL_SEC, "updated_at": int(_cache["ts"])}


def weather_url_preview():
    store = get_store()
    names = list(store.district_center.keys())
    lats = ",".join(f"{store.district_center[n][1]:.4f}" for n in names)
    lons = ",".join(f"{store.district_center[n][0]:.4f}" for n in names)
    return (f"{settings.OPEN_METEO_URL}?latitude={lats}&longitude={lons}"
            "&current=precipitation&hourly=precipitation&forecast_days=2&timezone=Asia%2FShanghai")
