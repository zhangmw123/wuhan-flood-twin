"""空间分析服务：内涝淹没推演、承灾体叠加评估、避障转移路径、缓冲区分析。

local 模式使用纯 Python 几何引擎（circle 距离判定 + 网格索引 + A*），
与 SQL 版 PostGIS 实现（backend/sql/init.sql）保持同一套计算口径。
"""
import math
import time

from ..core import geo
from ..core.config import settings
from ..core.store import get_store

_CACHE = {}
_CACHE_MAX = 24


# ------------------------------------------------------------------ 淹没范围


def _station_depth_for_rainfall(s, rainfall_mm, duration_h, store):
    """独立于沙盘时钟的雨量情景：用恒定雨强做区级水量平衡积分。"""
    district = s["district"]
    area = store.district_area.get(district, 1e6)
    intensity = rainfall_mm / max(duration_h, 0.5)
    net = store._district_net_cm_h(district, intensity, area)
    if net <= 0:
        return 0.0
    lf = store._local_factor(s)
    depth = net * duration_h * lf
    return min(depth, store._cap(s))


def station_scenario_depths(rainfall_mm, duration_h, district=None, min_depth=0.0):
    """情景推演下各站点的积水深度（与灾情评估同一套公式）。

    供简报、排行等场景使用，避免"情景雨量"与"沙盘时刻深度"两套口径混用。
    """
    store = get_store()
    out = []
    for st in store.waterlogging:
        if district and st["district"] != district:
            continue
        depth = _station_depth_for_rainfall(st, rainfall_mm or 0, duration_h, store)
        if depth < min_depth:
            continue
        out.append({
            "station_code": st["station_code"], "name": st["name"],
            "district": st["district"], "lng": st["lng"], "lat": st["lat"],
            "pump_name": st.get("pump_name"), "depth_cm": round(depth, 1),
        })
    out.sort(key=lambda x: -x["depth_cm"])
    return out


def build_flood_zones(rainfall_mm=None, duration_h=3, district=None, min_depth=15.0, use_state=False):
    """生成淹没区（圆形近似，半径 = 积水深度 × 扩散系数）。

    返回 [{station, depth_cm, radius_m, ring, bbox}]，同时返回 GeoJSON。
    """
    store = get_store()
    zones = []
    for s in store.waterlogging:
        if district and s["district"] != district:
            continue
        if use_state:
            depth = store.depths.get(s["station_code"], 0.0)
        else:
            depth = _station_depth_for_rainfall(s, rainfall_mm or 0, duration_h, store)
        if depth < min_depth:
            continue
        radius = min(depth * settings.FLOOD_SPREAD_FACTOR, 900.0)
        ring = geo.circle_ring(s["lng"], s["lat"], radius, segments=40)
        zones.append({
            "station": s["name"], "code": s["station_code"], "district": s["district"],
            "depth_cm": round(depth, 1), "radius_m": round(radius, 1),
            "center": [s["lng"], s["lat"]], "ring": ring,
            "bbox": geo.bbox_of(ring), "pump": s.get("pump_name"),
        })
    return zones


def zones_geojson(zones):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [z["ring"]]},
            "properties": {
                "station": z["station"], "station_code": z["code"], "district": z["district"],
                "depth_cm": z["depth_cm"], "radius_m": z["radius_m"],
                "area_km2": round(math.pi * (z["radius_m"] ** 2) / 1e6, 3),
            },
        } for z in zones],
    }


# ------------------------------------------------------------------ 承灾体叠加


def _buildings_in_zones(zones, limit=4000):
    store = get_store()
    hit = {}
    for z in zones:
        cx, cy = z["center"]
        r = z["radius_m"]
        dlat = r / 110540.0
        dlon = r / (111320.0 * math.cos(math.radians(cy)) or 1e-6)
        box = [cx - dlon, cy - dlat, cx + dlon, cy + dlat]
        for b in store.building_index.query_bbox(box):
            if b["id"] in hit:
                hit[b["id"]]["depth_cm"] = max(hit[b["id"]]["depth_cm"], z["depth_cm"])
                continue
            dx = (b["lng"] - cx) * 111320.0 * math.cos(math.radians(cy))
            dy = (b["lat"] - cy) * 110540.0
            if dx * dx + dy * dy <= r * r:
                hit[b["id"]] = {**b, "zone": z["station"], "depth_cm": z["depth_cm"]}
    items = sorted(hit.values(), key=lambda x: -x["depth_cm"])
    return items, len(hit), items[:limit]


def _roads_in_zones(zones):
    store = get_store()
    blocked = {}
    for z in zones:
        cx, cy = z["center"]
        r = z["radius_m"]
        for road in store.road_index.query_bbox(z["bbox"]):
            rid = road["id"]
            if rid in blocked:
                continue
            # bbox 快速剔除
            b = road["bbox"]
            dx = max(b[0] - cx, 0, cx - b[2]) * 111320.0 * math.cos(math.radians(cy))
            dy = max(b[1] - cy, 0, cy - b[3]) * 110540.0
            if dx * dx + dy * dy > r * r:
                continue
            length_in = _line_in_circle(road["coords"], cx, cy, r)
            if length_in >= settings.ROAD_BLOCK_MIN_LEN_M:
                rid_set = blocked.setdefault(rid, {"road": road, "len_in": 0.0, "zones": set()})
                rid_set["len_in"] = max(rid_set["len_in"], length_in)
                rid_set["zones"].add(z["station"])
    return list(blocked.values()), len(blocked)


def _line_in_circle(coords, cx, cy, r):
    """折线落在圆内的长度(m)，按 15m 步长采样。"""
    total = 0.0
    coslat = math.cos(math.radians(cy))
    r2 = r * r
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        seg = geo.haversine(a, b)
        if seg <= 0:
            continue
        n = max(1, int(seg / 15.0))
        inside = 0
        for k in range(n):
            t = (k + 0.5) / n
            x = (a[0] + (b[0] - a[0]) * t - cx) * 111320.0 * coslat
            y = (a[1] + (b[1] - a[1]) * t - cy) * 110540.0
            if x * x + y * y <= r2:
                inside += 1
        total += seg * inside / n
    return total


def _points_in_zones(features, zones):
    out = []
    for f in features:
        g = f.get("geometry")
        if not g or g.get("type") != "Point":
            continue
        x, y = g["coordinates"][0], g["coordinates"][1]
        for z in zones:
            cx, cy = z["center"]
            dx = (x - cx) * 111320.0 * math.cos(math.radians(cy))
            dy = (y - cy) * 110540.0
            if dx * dx + dy * dy <= z["radius_m"] ** 2:
                out.append({**f["properties"], "zone": z["station"], "depth_cm": z["depth_cm"]})
                break
    return out


def impact_analysis(rainfall_mm=120.0, duration_h=3, district=None, use_state=False, detail_limit=200):
    """核心灾情评估：淹没区 -> 受淹建筑/阻断道路/重点单位 空间叠加统计。"""
    key = (round(float(rainfall_mm), 1), round(float(duration_h), 1), district, use_state,
           round(get_store().hour, 1) if use_state else None)
    cached = _CACHE.get(key)
    if cached:
        return cached

    t0 = time.perf_counter()
    store = get_store()
    zones = build_flood_zones(rainfall_mm, duration_h, district, use_state=use_state)
    buildings, bcount, bsample = _buildings_in_zones(zones)
    blocked, rcount = _roads_in_zones(zones)
    shelters_hit = _points_in_zones(store.shelters["features"], zones)
    poi_hit = _points_in_zones(store.key_poi["features"], zones)

    area_km2 = sum(math.pi * z["radius_m"] ** 2 for z in zones) / 1e6
    road_len_km = sum(b["len_in"] for b in blocked) / 1000.0
    by_district = {}
    for b in buildings:
        d = b["district"] or "未知"
        rec = by_district.setdefault(d, {"district": d, "buildings": 0, "low_elev": 0})
        rec["buildings"] += 1
        if b["elevation"] <= 20:
            rec["low_elev"] += 1

    result = {
        "code": 200,
        "data": {
            "query": {"rainfall_mm": rainfall_mm, "duration_hours": duration_h, "district": district},
            "inundated_area_km2": round(area_km2, 2),
            "zone_count": len(zones),
            "affected_building_count": bcount,
            "affected_building_area_wan_m2": round(sum(b["area_m2"] for b in buildings) / 1e4, 1),
            "blocked_road_count": rcount,
            "blocked_road_length_km": round(road_len_km, 2),
            "threatened_shelter_count": len(shelters_hit),
            "threatened_key_poi_count": len(poi_hit),
            "max_depth_cm": max([z["depth_cm"] for z in zones], default=0),
            "avg_depth_cm": round(sum(z["depth_cm"] for z in zones) / len(zones), 1) if zones else 0,
            "inundation_polygons_geojson": zones_geojson(zones),
            "blocked_roads_geojson": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": b["road"]["coords"]},
                    "properties": {
                        "id": b["road"]["id"], "name": b["road"]["name"] or "无名道路",
                        "cls": b["road"]["cls"], "blocked_len_m": round(b["len_in"], 1),
                        "zones": "、".join(sorted(b["zones"]))[:120],
                    },
                } for b in blocked[:1200]],
            },
            "affected_buildings": [{
                "id": b["id"], "lng": b["lng"], "lat": b["lat"],
                "elevation": b["elevation"], "district": b["district"],
                "zone": b["zone"], "depth_cm": b["depth_cm"],
            } for b in bsample[:detail_limit]],
            "district_breakdown": sorted(by_district.values(), key=lambda x: -x["buildings"]),
            "threatened_poi": poi_hit[:60],
            "threatened_shelters": shelters_hit[:30],
            "compute_ms": int((time.perf_counter() - t0) * 1000),
            "engine": settings.SPATIAL_ENGINE,
        },
    }
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = result
    return result


# ------------------------------------------------------------------ 避险路径


_GRAPH_CACHE = {"key": None, "nodes": None, "adj": None, "roads": None}


def _roads_near(lng, lat, radius_m):
    store = get_store()
    coslat = math.cos(math.radians(lat))
    box = [lng - radius_m / (111320 * coslat), lat - radius_m / 110540,
           lng + radius_m / (111320 * coslat), lat + radius_m / 110540]
    roads = store.road_index.query_bbox(box)
    return roads, box


def evacuation_route(origin_lng, origin_lat, avoid_waterlogging=True, max_candidates=4):
    """最近的、可达的应急避难场所路径规划（避开积水阻断路段）。"""
    t0 = time.perf_counter()
    store = get_store()
    zones = build_flood_zones(use_state=True, min_depth=25.0) if avoid_waterlogging else []
    blocked_zones = _roads_in_zones(zones)[0] if zones else []
    blocked_ids = frozenset(b["road"]["id"] for b in blocked_zones)

    # 候选避难所：6km 内按直线距离排序
    cands = []
    for f in store.shelters["features"]:
        x, y = f["geometry"]["coordinates"]
        d = geo.haversine([origin_lng, origin_lat], [x, y])
        if d <= 6000:
            cands.append({"shelter": f["properties"], "lng": x, "lat": y, "straight_m": d})
    cands.sort(key=lambda c: c["straight_m"])
    if not cands:
        return {"code": 404, "message": "6 公里内没有可用的应急避难场所"}

    roads, box = _roads_near(origin_lng, origin_lat, 6500)
    gkey = (round(box[0], 3), round(box[1], 3), round(box[2], 3), round(box[3], 3))
    if _GRAPH_CACHE["key"] != gkey:
        nodes, adj = geo.build_graph(roads, box)
        _GRAPH_CACHE.update(key=gkey, nodes=nodes, adj=adj, roads=roads)
    nodes, adj = _GRAPH_CACHE["nodes"], _GRAPH_CACHE["adj"]

    start = geo.nearest_node(nodes, origin_lng, origin_lat)
    best = None
    # 第一轮严格避开积水路段；不可达时第二轮允许降级涉水通行（代价 ×8）
    for hard, penalty in ((True, 1.0), (False, 8.0)):
        for c in cands[:max_candidates]:
            goal = geo.nearest_node(nodes, c["lng"], c["lat"])
            path, dist = geo.astar(nodes, adj, start, goal, blocked_ids if hard else frozenset(), penalty)
            if path and (best is None or dist < best["dist"]):
                best = {"dist": dist, "path": path, "cand": c, "goal": goal, "hard": hard}
        if best is not None:
            break

    if best is None:  # 路网不可达时退化为直线引导
        c = cands[0]
        path = [[origin_lng, origin_lat], [c["lng"], c["lat"]]]
        dist = c["straight_m"]
        best = {"dist": dist, "path": path, "cand": c, "goal": None, "fallback": True}

    path = geo.simplify_path(best["path"], 6.0)
    walk_min = best["dist"] / 1.25 / 60.0       # 1.25 m/s
    drive_min = best["dist"] / (28 * 1000 / 3600) / 60.0

    # 途经的危险点（路线 120m 内的高水位站点）
    bypassed = []
    for s in store.waterlogging:
        depth = store.depths.get(s["station_code"], 0)
        if depth < 25:
            continue
        for p in path:
            if geo.haversine(p, [s["lng"], s["lat"]]) <= 120:
                bypassed.append(s["name"])
                break

    sh = best["cand"]["shelter"]
    return {
        "code": 200,
        "data": {
            "target_shelter": {
                "id": sh.get("name"), "name": sh.get("name"), "address": sh.get("address"),
                "district": sh.get("district"), "capacity": sh.get("capacity"),
                "lng": best["cand"]["lng"], "lat": best["cand"]["lat"],
                "distance_m": int(best["dist"]), "straight_m": int(best["cand"]["straight_m"]),
            },
            "estimated_time_min": max(1, int(round(walk_min))),
            "estimated_drive_min": max(1, int(round(drive_min))),
            "bypassed_danger_points": bypassed,
            "avoid_blocked_roads": len(blocked_ids),
            "route_geojson": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": path},
                "properties": {"distance_m": int(best["dist"]), "fallback": best.get("fallback", False)},
            },
            "origin": [origin_lng, origin_lat],
            "compute_ms": int((time.perf_counter() - t0) * 1000),
        },
    }


# ------------------------------------------------------------------ 缓冲区


def buffer_analysis(lng, lat, radius_m=500):
    """AI 助手的「生成 N 米缓冲分析」：统计圈内建筑、避难所、重点单位、水系。"""
    store = get_store()
    ring = geo.circle_ring(lng, lat, radius_m, 36)
    bbox = geo.bbox_of(ring)
    coslat = math.cos(math.radians(lat))
    buildings = []
    for b in store.building_index.query_bbox(bbox):
        dx = (b["lng"] - lng) * 111320.0 * coslat
        dy = (b["lat"] - lat) * 110540.0
        if dx * dx + dy * dy <= radius_m ** 2:
            buildings.append(b)
    shelters = []
    for f in store.shelters["features"]:
        x, y = f["geometry"]["coordinates"]
        d = geo.haversine([lng, lat], [x, y])
        if d <= radius_m:
            shelters.append({**f["properties"], "distance_m": int(d)})
    shelters.sort(key=lambda s: s["distance_m"])
    pois = []
    for f in store.key_poi["features"]:
        x, y = f["geometry"]["coordinates"]
        d = geo.haversine([lng, lat], [x, y])
        if d <= radius_m:
            pois.append({**f["properties"], "distance_m": int(d)})
    pois.sort(key=lambda s: s["distance_m"])

    return {
        "center": [lng, lat], "radius_m": radius_m,
        "building_count": len(buildings),
        "building_total_area_m2": round(sum(b["area_m2"] for b in buildings)),
        "avg_elevation": round(sum(b["elevation"] for b in buildings) / len(buildings), 1) if buildings else 0,
        "shelter_count": len(shelters),
        "nearest_shelter": shelters[0] if shelters else None,
        "poi_count": len(pois),
        "pois": pois[:8],
        "buffer_geojson": {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"radius_m": radius_m},
        },
        "buildings": [{"id": b["id"], "lng": b["lng"], "lat": b["lat"], "elevation": b["elevation"]}
                      for b in buildings[:400]],
    }


# ------------------------------------------------------------------ 区级统计


def district_stats():
    store = get_store()
    out = []
    for name in store.district_area:
        sts = [s for s in store.waterlogging if s["district"] == name]
        depths = [store.depths.get(s["station_code"], 0) for s in sts]
        out.append({
            "district": name,
            "rain_1h_mm": round(store.district_rain.get(name, 0), 1),
            "station_count": len(sts),
            "alert_count": sum(1 for d in depths if d >= 30),
            "danger_count": sum(1 for d in depths if d >= 50),
            "max_depth_cm": round(max(depths), 1) if depths else 0,
            "avg_depth_cm": round(sum(depths) / len(depths), 1) if depths else 0,
            "pump_capacity_m3s": round(store.pump_by_district.get(name, 0), 1),
            "area_km2": round(store.district_area.get(name, 0) / 1e6, 1),
        })
    return sorted(out, key=lambda x: -x["max_depth_cm"])


def water_level_series():
    """沙盘 24 小时武汉关水位-流量过程线（用于左侧图表）。"""
    store = get_store()
    out = []
    cum = 0.0
    for h in range(0, 25):
        if h < 24:
            cum += store.rain_at(h)
        level = round(26.85 + cum * 0.00625, 2)
        flow = round(38000 + cum * 42, 0)
        out.append({"hour": f"{h:02d}:00", "level_m": level, "flow_m3s": flow})
    return out
