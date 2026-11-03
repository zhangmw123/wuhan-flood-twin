"""按「关键水体清单」从 Overpass 精确抓取武汉真实河湖面。

为什么不做全量网格抓取：武汉属水体密集区，单次 bbox 扫描需 60~120s，
同 IP 并发还会被限流（实测 6 线程反而比 3 线程慢）。最省的路径是
「按名称 + 小 bbox」，每个湖一次查询即可拿到完整 relation。

实测：东湖 1 次查询 ≈ 100s，返回 61 个 outer 成员环（完整湖面）。

用法：
    python scripts/03_fetch_osm_water.py            # 抓清单内水体（带缓存，可重复跑）
    python scripts/03_fetch_osm_water.py --force    # 忽略缓存
    python scripts/03_fetch_osm_water.py --list     # 只打印清单

输出：data/processed/wuhan_water.geojson（与手工示意数据同结构，前端无需改动）
许可：© OpenStreetMap contributors, ODbL 1.0
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "processed")
CACHE_DIR = os.path.join(OUT_DIR, "_osm_cache")

# (显示名, 查询名, bbox[minLat,minLon,maxLat,maxLon])
TARGETS = [
    ("东湖", "东湖", (30.49, 114.33, 30.61, 114.45)),
    ("沙湖", "沙湖", (30.54, 114.30, 30.60, 114.38)),
    ("南湖", "南湖", (30.45, 114.30, 30.53, 114.39)),
    ("汤逊湖", "汤逊湖", (30.37, 114.27, 30.47, 114.41)),
    ("黄家湖", "黄家湖", (30.40, 114.24, 30.48, 114.33)),
    ("野芷湖", "野芷湖", (30.44, 114.27, 30.50, 114.35)),
    ("墨水湖", "墨水湖", (30.51, 114.20, 30.57, 114.29)),
    ("月湖", "月湖", (30.53, 114.23, 30.58, 114.28)),
    ("后官湖", "后官湖", (30.46, 114.02, 30.60, 114.20)),
    ("金银湖", "金银湖", (30.61, 114.13, 30.71, 114.25)),
    ("严西湖", "严西湖", (30.51, 114.43, 30.61, 114.53)),
    ("青菱湖", "青菱湖", (30.36, 114.16, 30.45, 114.27)),
    ("紫阳湖", "紫阳湖", (30.52, 114.28, 30.56, 114.32)),
    ("沉湖", "沉湖", (30.28, 113.88, 30.40, 114.02)),
]

# 三条线路：每线程固定一个端点，避免同端点并发被限流
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
UA = "wuhan-flood-twin/1.0 (flood digital twin; research use)"

_lock = threading.Lock()
results: dict[str, list] = {}


def build_query(name_re: str, bbox) -> str:
    s, w, n, e = bbox
    body = (f'nwr["natural"="water"]["name"~"^{name_re}$"]({s},{w},{n},{e});'
            f'way["waterway"="riverbank"]["name"~"^{name_re}"]({s},{w},{n},{e});')
    return f"[out:json][timeout:180];({body});out geom;"


def fetch(name: str, name_re: str, bbox, ep: str, timeout=240) -> dict | None:
    payload = urllib.parse.urlencode({"data": build_query(name_re, bbox)}).encode()
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                ep, data=payload, method="POST",
                headers={"User-Agent": UA,
                         "Content-Type": "application/x-www-form-urlencoded",
                         "Accept": "application/json"},
            )
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name}] {ep.split('/')[2]} 失败 {type(exc).__name__}，重试…", flush=True)
            time.sleep(3)
    return None


def ring_area_km2(ring):
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i][0] * kx, ring[i][1] * ky
        x2, y2 = ring[(i + 1) % len(ring)][0] * kx, ring[(i + 1) % len(ring)][1] * ky
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def to_rings(el):
    """返回若干闭合环；relation 需按端点顺序拼接 outer 成员。"""
    if el["type"] == "way":
        pts = [[round(p["lon"], 6), round(p["lat"], 6)] for p in (el.get("geometry") or []) if p]
        return [pts] if len(pts) >= 3 else []

    rings, cur = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or m.get("role") not in ("outer", ""):
            continue
        pts = [[round(p["lon"], 6), round(p["lat"], 6)] for p in (m.get("geometry") or []) if p]
        if not pts:
            continue
        if not cur:
            cur = pts
        elif cur[-1] == pts[0]:
            cur += pts[1:]
        elif cur[-1] == pts[-1]:
            cur += list(reversed(pts))[1:]
        else:
            rings.append(cur)
            cur = pts
    if cur:
        rings.append(cur)
    return [r for r in rings if len(r) >= 3]


def worker(ep: str, jobs: queue.Queue, force: bool):
    while True:
        try:
            display, name_re, bbox = jobs.get_nowait()
        except queue.Empty:
            return
        cache = os.path.join(CACHE_DIR, f"{display}.json")
        data = None
        if os.path.exists(cache) and not force:
            try:
                data = json.load(open(cache, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            t = time.time()
            data = fetch(display, name_re, bbox, ep)
            if data is not None:
                json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
                print(f"  [{display}] {len(data['elements'])} 要素 / {round(time.time() - t)}s", flush=True)
        else:
            print(f"  [{display}] 命中缓存", flush=True)
        if data is not None:
            with _lock:
                results[display] = data.get("elements", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for d, _, b in TARGETS:
            print(f"{d:<8} {b}")
        return

    os.makedirs(CACHE_DIR, exist_ok=True)
    jobs: queue.Queue = queue.Queue()
    for t in TARGETS:
        jobs.put(t)

    threads = [threading.Thread(target=worker, args=(ep, jobs, args.force), daemon=True)
               for ep in ENDPOINTS]
    t0 = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    print(f"抓取结束，耗时 {round(time.time() - t0)}s，命中 {len(results)}/{len(TARGETS)}", flush=True)

    features = []
    for display, elements in results.items():
        rings_out = []
        for el in elements:
            tags = el.get("tags", {})
            real_name = (tags.get("name") or display).strip()
            for ring in to_rings(el):
                area = ring_area_km2(ring)
                if area < 0.005:  # 过滤水塘与构图碎片
                    continue
                if ring[0] != ring[-1]:
                    ring = ring + [ring[0]]
                rings_out.append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "name": display if real_name == display else f"{display}({real_name})",
                        "water_type": "lake", "width_m": None,
                        "area_km2": round(area, 4),
                        "osm_id": el.get("id"), "osm_type": el["type"],
                        "source": "OpenStreetMap/ODbL",
                    },
                })
        if not rings_out:
            print(f"  ! {display} 无有效环", flush=True)
            continue
        rings_out.sort(key=lambda f: -f["properties"].get("area_km2", 0))
        features.extend(rings_out[:6])  # 单湖最多保留 6 个环（含湖汊/子湖）

    # 长江/汉江干流保留手工矢量化：OSM riverbank 沿江分段极长，单次抓取代价过高
    river_path = os.path.join(OUT_DIR, "wuhan_rivers_manual.geojson")
    if os.path.exists(river_path):
        rivers = json.load(open(river_path, encoding="utf-8"))["features"]
        features.extend(rivers)
        print(f"  合并长江/汉江干流 {len(rivers)} 段（手工矢量化）", flush=True)

    features.sort(key=lambda f: -f["properties"].get("area_km2", 0))
    out = {"type": "FeatureCollection", "name": "wuhan_water_osm",
           "license": "© OpenStreetMap contributors, ODbL 1.0",
           "features": features}
    path = os.path.join(OUT_DIR, "wuhan_water.geojson")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"\n输出 {len(features)} 个水面，{os.path.getsize(path) / 1024:.0f} KB -> {path}", flush=True)
    for f in features[:12]:
        p = f["properties"]
        print(f"   {p['name']:<16} {p.get('area_km2', 0):8.2f} km²  ({p['osm_type']})", flush=True)


if __name__ == "__main__":
    main()
