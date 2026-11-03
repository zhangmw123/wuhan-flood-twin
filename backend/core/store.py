"""数据仓储：加载预处理成果，构建空间索引，维护水雨情推演状态。

纯标准库实现，服务启动时一次性载入内存（约 2~4 秒）：
  建筑 20.6 万（中心点 + 高度 + 面积 + 所属区）
  道路 2.9 万条（含几何）
  避难所 172、重点防护点 376、渍水点/水文站/泵站 76、行政区 13、水系 10
"""
import csv
import json
import os
import threading
import time

from . import geo
from .config import PROCESSED_DIR, TILE_DIR, settings


def _read(path):
    with open(os.path.join(PROCESSED_DIR, path), "r", encoding="utf-8") as fp:
        return json.load(fp)


def _bbox_of_geojson(g):
    if not g:
        return None
    return geo.bbox_of(g["coordinates"])


# 2016·7·6 沙盘：逐时雨强(mm/h)，24 小时累计约 240mm，峰值 83mm/h
SCENARIO_CURVE = [
    0.5, 0.5, 0.5, 1.0, 2.0, 3.0, 5.0, 12.0, 40.0, 83.0, 45.0, 20.0,
    10.0, 6.0, 4.0, 3.0, 2.0, 1.5, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5,
]

# 各区的城市化强度系数（中心城区产流快、汇水集中）
DISTRICT_FACTOR = {
    "江岸区": 1.08, "江汉区": 1.12, "硚口区": 1.10, "汉阳区": 1.05, "武昌区": 1.10,
    "洪山区": 1.02, "青山区": 0.98, "东西湖区": 0.92, "蔡甸区": 0.88, "江夏区": 0.90,
    "黄陂区": 0.82, "新洲区": 0.80, "汉南区": 0.78,
}


class Store:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        t0 = time.time()
        self.meta = _read("meta.json")
        self.districts = _read("wuhan_districts.geojson")
        self.water = _read("wuhan_water.geojson")
        self.shelters = _read("wuhan_shelters.geojson")
        self.key_poi = _read("wuhan_key_poi.geojson")
        self.stations = _read("wuhan_waterlogging.geojson")

        self._load_districts()
        self._load_buildings()
        self._load_roads()
        self._load_stations()

        self.mode = "simulation"
        self.hour = 8.0          # 沙盘时钟（0~24）
        self.playing = True
        self.last_tick = time.time()
        self.depths = {}         # station_code -> depth_cm
        self.district_rain = {}
        self.observed = {}       # 真实感知数据覆盖值：station_code -> depth_cm
        self.observed_ts = None  # 最近一次真实数据到达时间
        self.observed_source = None
        self.hankou_level = settings.HANKOU_WARNING_LEVEL - 0.4
        self.load_seconds = round(time.time() - t0, 2)
        self.recompute()

    # ------------------------------------------------------------ 载入

    def _load_districts(self):
        self.district_geom = {}
        self.district_area = {}
        self.district_bbox = {}
        self.district_center = {}
        for f in self.districts["features"]:
            name = f["properties"]["name"]
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            area = 0.0
            for rings in polys:
                if rings:
                    area += geo.ring_area_m2(rings[0])
            bb = _bbox_of_geojson(g)
            self.district_geom[name] = f
            self.district_area[name] = max(area, 1.0)
            self.district_bbox[name] = bb
            self.district_center[name] = [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]

    def _load_buildings(self):
        self.buildings = []
        self.building_index = geo.GridIndex(cell=0.004)
        with open(os.path.join(PROCESSED_DIR, "buildings.csv"), encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                b = {
                    "id": int(row["id"]),
                    "lng": float(row["lng"]),
                    "lat": float(row["lat"]),
                    "elevation": float(row["elevation"]),
                    "area_m2": float(row["area_m2"]),
                    "district": row["district"],
                }
                self.buildings.append(b)
                self.building_index.insert(b, b["lng"], b["lat"])

    def _load_roads(self):
        self.roads = []
        self.road_index = geo.GridIndex(cell=0.01)
        with open(os.path.join(PROCESSED_DIR, "roads.jsonl"), encoding="utf-8") as fp:
            for line in fp:
                r = json.loads(line)
                r["bbox"] = geo.bbox_of(r["coords"])
                self.roads.append(r)
                self.road_index.insert_bbox(r, r["bbox"])

    def _load_stations(self):
        self.waterlogging, self.hydrology, self.pumps = [], [], []
        for f in self.stations["features"]:
            p = f["properties"]
            rec = dict(p)
            rec["lng"], rec["lat"] = f["geometry"]["coordinates"]
            rec["type"] = p["station_type"]
            {"waterlogging": self.waterlogging, "hydrology": self.hydrology,
             "pump": self.pumps}[p["station_type"]].append(rec)

        # 各区排涝能力(m³/s) = 区内泵站容量之和
        self.pump_by_district = {}
        for p in self.pumps:
            self.pump_by_district[p["district"]] = self.pump_by_district.get(p["district"], 0.0) + float(p["capacity_m3s"] or 0)

    # ------------------------------------------------------------ 推演状态

    def set_mode(self, mode):
        if mode in ("realtime", "simulation"):
            self.mode = mode
            self.recompute()

    def set_hour(self, hour):
        self.hour = max(0.0, min(24.0, float(hour)))
        self.recompute()

    def tick(self, seconds=3.0):
        """沙盘时钟推进：每 3 秒 = 10 分钟情景时间。"""
        if self.mode != "simulation" or not self.playing:
            return
        now = time.time()
        elapsed = now - self.last_tick
        if elapsed < seconds:
            return
        self.last_tick = now
        self.hour = (self.hour + elapsed / seconds * (10.0 / 60.0)) % 24.0
        self.recompute()

    def rain_at(self, hour, district=None):
        """情景时刻的瞬时雨强(mm/h)。"""
        idx = int(hour) % 24
        nxt = (idx + 1) % 24
        frac = hour - int(hour)
        base = SCENARIO_CURVE[idx] * (1 - frac) + SCENARIO_CURVE[nxt] * frac
        if district is None:
            return base
        return base * DISTRICT_FACTOR.get(district, 0.95)

    def cum_rain(self, hour, district=None):
        """累计雨量(mm)：分别返回截至当前时刻的近 3h 与近 24h 累计。"""
        c3 = c24 = 0.0
        for h in range(0, 24):
            v = self.rain_at(h, district)
            if h < hour:
                c24 += v * min(1.0, hour - h)
            if hour - 3 <= h < hour:
                c3 += v * min(1.0, hour - h)
        return c3, c24

    def recompute(self):
        """按当前模式与时钟，积分得到各区积水深度、站点深度与武汉关水位。"""
        if self.mode == "simulation":
            self._simulate()
        else:
            self._realtime()

    def _simulate(self):
        dt = 0.1
        depths = {s["station_code"]: 0.0 for s in self.waterlogging}
        steps = max(1, int(self.hour / dt))
        for k in range(steps):
            h = (k + 1) * dt
            for d, area in self.district_area.items():
                rain = self.rain_at(h, d)
                net = self._district_net_cm_h(d, rain, area)
                if net <= 0:
                    continue
                for s in self.waterlogging:
                    if s["district"] != d:
                        continue
                    lf = self._local_factor(s)
                    code = s["station_code"]
                    depths[code] = max(0.0, depths[code] + (net * lf - settings.NATURAL_DRAIN_CM_H) * dt)
                    depths[code] = min(depths[code], self._cap(s))
        self.depths = {k: round(v, 1) for k, v in depths.items()}
        self.district_rain = {d: round(self.rain_at(self.hour, d), 1) for d in self.district_area}
        _, c24 = self.cum_rain(self.hour)
        self.hankou_level = round(26.85 + c24 * 0.00625, 2)
        self._apply_observed()

    def _realtime(self):
        from ..services.weather import get_realtime_rain
        rain = get_realtime_rain()  # {district: mm/h}
        self.district_rain = rain
        depths = {}
        for s in self.waterlogging:
            r = rain.get(s["district"], 0.0)
            net = self._district_net_cm_h(s["district"], r, self.district_area.get(s["district"], 1e6))
            steady = max(0.0, net * 2.0) * self._local_factor(s)  # 2 小时滞时近似稳态
            depths[s["station_code"]] = round(min(steady, self._cap(s)), 1)
        self.depths = depths
        big = max(rain.values() or [0])
        self.hankou_level = round(26.9 + big * 0.012, 2)
        self._apply_observed()

    # ------------------------------------------------------------ 真实感知数据接入
    def apply_readings(self, readings, source="manual"):
        """用真实观测值覆盖模型推演值。

        readings: {station_code: depth_cm}；传空 dict 表示撤销覆盖、回到模型值。
        这是「感知层」的唯一注入点 —— 接水位计 / 视频识别 / 交警封路数据时只改这里，
        前端与接口无需改动。
        """
        clean = {}
        for code, depth in (readings or {}).items():
            try:
                clean[str(code)] = max(0.0, float(depth))
            except (TypeError, ValueError):
                continue
        self.observed = clean
        self.observed_ts = time.time() if clean else None
        self.observed_source = source if clean else None
        self.recompute()

    def _apply_observed(self):
        if not self.observed:
            return
        for code, depth in self.observed.items():
            if code in self.depths:
                self.depths[code] = round(depth, 1)

    def _district_net_cm_h(self, district, rain_mm_h, area_m2):
        """区级水量平衡：产流(m³/h) - 泵排(m³/h) -> 平均水深增速(cm/h)。"""
        runoff = rain_mm_h / 1000.0 * area_m2 * settings.RUNOFF_COEFF
        pump = self.pump_by_district.get(district, 0.0) * 3600.0
        return (runoff - pump) / area_m2 * 100.0

    @staticmethod
    def _local_factor(station):
        """点位低洼程度系数：设计积水越深代表地形越易汇水；
        下穿通道/隧道/立交为封闭洼地，汇水集中度再提高 45%。"""
        design = float(station.get("design_depth_cm") or 40)
        factor = max(1.5, min(6.2, design / 16.0))
        if any(k in station.get("name", "") for k in ("隧道", "下穿", "立交", "通道", "涵洞")):
            factor *= 1.45
        return factor

    @staticmethod
    def _cap(station):
        design = float(station.get("design_depth_cm") or 40)
        return max(62.0, design * 2.0)

    # ------------------------------------------------------------ 派生查询

    def station_payload(self):
        """站点当前读数（含预警等级）→ GeoJSON。"""
        feats = []
        for s in self.waterlogging:
            depth = self.depths.get(s["station_code"], 0.0)
            warn = float(s.get("warning_threshold_cm") or 30)
            danger = float(s.get("danger_threshold_cm") or 50)
            level = 3 if depth >= danger else 2 if depth >= danger * 0.8 else 1 if depth >= warn else 0
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
                "properties": {
                    "station_code": s["station_code"], "name": s["name"],
                    "district": s["district"], "station_type": "waterlogging",
                    "current_depth_cm": round(depth, 1),
                    "rain_1h_mm": round(self.district_rain.get(s["district"], 0.0), 1),
                    "alert_level": level,
                    "warning_threshold_cm": warn, "danger_threshold_cm": danger,
                    "pump_name": s.get("pump_name"),
                },
            })
        for s in self.hydrology:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
                "properties": {
                    "station_code": s["station_code"], "name": s["name"], "district": s["district"],
                    "station_type": "hydrology",
                    "current_level_m": self.hankou_level if "汉口" in s["name"] else round(self.hankou_level - 7.2, 2),
                    "warning_level_m": s.get("warning_level_m"),
                    "guarantee_level_m": s.get("guarantee_level_m"),
                    "alert_level": 3 if self.hankou_level >= settings.HANKOU_GUARANTEE_LEVEL else
                                   2 if self.hankou_level >= settings.HANKOU_WARNING_LEVEL + 0.5 else
                                   1 if self.hankou_level >= settings.HANKOU_WARNING_LEVEL else 0,
                },
            })
        for s in self.pumps:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
                "properties": {
                    "station_code": s["station_code"], "name": s["name"], "district": s["district"],
                    "station_type": "pump", "capacity_m3s": s.get("capacity_m3s"),
                    "running": self.district_rain.get(s["district"], 0) > 8,
                },
            })
        return {"type": "FeatureCollection", "features": feats}

    def station_meta(self):
        worst = sorted(self.waterlogging, key=lambda s: -self.depths.get(s["station_code"], 0))[:8]
        return {
            "mode": self.mode,
            "hour": round(self.hour, 2),
            "playing": self.playing,
            "data_source": "observed" if self.observed else "model",
            "observed_station_count": len(self.observed),
            "observed_age_sec": round(time.time() - self.observed_ts, 1) if self.observed_ts else None,
            "observed_provider": self.observed_source,
            "city_avg_rain_1h_mm": round(sum(self.district_rain.values()) / max(1, len(self.district_rain)), 1),
            "hankou_water_level_m": self.hankou_level,
            "hankou_warning_level_m": settings.HANKOU_WARNING_LEVEL,
            "alert_station_count": sum(1 for v in self.depths.values() if v >= 30),
            "danger_station_count": sum(1 for v in self.depths.values() if v >= 50),
            "top_stations": [{
                "name": s["name"], "district": s["district"],
                "depth_cm": round(self.depths.get(s["station_code"], 0), 1),
                "pump": s.get("pump_name"),
            } for s in worst],
        }

    def alerts(self):
        """超阈值站点列表，用于 SSE 推送。"""
        out = []
        for s in self.waterlogging:
            depth = self.depths.get(s["station_code"], 0)
            warn = float(s.get("warning_threshold_cm") or 30)
            danger = float(s.get("danger_threshold_cm") or 50)
            if depth < warn:
                continue
            level = 3 if depth >= danger else 2 if depth >= danger * 0.8 else 1
            out.append({
                "station_code": s["station_code"], "name": s["name"], "district": s["district"],
                "current_depth_cm": round(depth, 1), "alert_level": level,
                "coordinates": [s["lng"], s["lat"]], "pump_name": s.get("pump_name"),
                "message": self._alert_message(s, depth, level, danger),
            })
        return out

    @staticmethod
    def _alert_message(s, depth, level, danger):
        tag = {3: "红色封路预警", 2: "橙色预警", 1: "黄色预警"}[level]
        if level == 3:
            return f"【{tag}】{s['name']}积水已达 {depth}cm，超过封路阈值({danger:.0f}cm)，已联动{s.get('pump_name')}强排！"
        if level == 2:
            return f"【{tag}】{s['name']}积水 {depth}cm，接近封路阈值，请提前布控。"
        return f"【{tag}】{s['name']}积水 {depth}cm，超过警戒阈值，请关注。"


def get_store():
    if Store._instance is None:
        with Store._lock:
            if Store._instance is None:
                Store._instance = Store()
    return Store._instance
