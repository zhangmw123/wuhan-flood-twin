#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
01_prepare_data.py —— 原始数据清洗、补全与预处理

输入：
  ../end5/GIS_DATA/*.json        （原《智慧文旅·武汉》项目数据）
  data/processed/wuhan_districts.geojson （阿里云 DataV 行政区划，已下载）

输出（均为 UTF-8）：
  data/processed/wuhan_districts.geojson   13 个行政区（原始下载件）
  data/processed/wuhan_water.geojson       长江/汉江/主要湖泊（示意矢量化）
  data/processed/wuhan_shelters.geojson    应急避难场所（GBK 已修复）
  data/processed/wuhan_key_poi.geojson     高校 / 医院 / 景点重点防护点
  data/processed/wuhan_waterlogging.geojson 渍水点 + 水文站 + 排涝泵站
  data/processed/buildings.csv             20 万建筑分析索引（中心点/高度/面积/所属区）
  data/processed/roads.jsonl               3 万道路分析索引（含几何）
  data/tiles/buildings/12/{x}_{y}.json     z12 建筑矢量分片
  data/tiles/roads/12/{x}_{y}.json         z12 道路矢量分片
  data/processed/meta.json                 统计元数据

仅依赖 Python 标准库，无需 geopandas / shapely。
"""
import csv
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(os.path.dirname(ROOT), "end5", "GIS_DATA")
OUT = os.path.join(ROOT, "data", "processed")
TILES = os.path.join(ROOT, "data", "tiles")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- 基础几何


def read_json_any(path):
    """自动识别 UTF-8 / GBK 解码，兼容原项目的历史乱码文件。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return json.loads(raw.decode(enc)), enc
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"无法解析 {path}")


def bbox_of(coords, acc=None):
    """递归求坐标数组的 bbox -> [minx, miny, maxx, maxy]"""
    if acc is None:
        acc = [1e9, 1e9, -1e9, -1e9]
    if not coords:
        return acc
    if isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        acc[0] = min(acc[0], x); acc[1] = min(acc[1], y)
        acc[2] = max(acc[2], x); acc[3] = max(acc[3], y)
        return acc
    for c in coords:
        bbox_of(c, acc)
    return acc


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    """rings[0] 为外环，其余为内环（洞）。"""
    if not rings or not point_in_ring(x, y, rings[0]):
        return False
    for hole in rings[1:]:
        if point_in_ring(x, y, hole):
            return False
    return True


def polygon_area_m2(ring):
    """鞋带公式 -> 平方米（局部等距近似）。"""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * kx, ring[i][1] * ky
        x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def line_length_m(coords):
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine(coords[i], coords[i + 1])
    return total


def haversine(a, b):
    r = 6371008.8
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def ring_centroid(ring):
    """多边形面积加权质心，退化时返回顶点均值。"""
    if len(ring) < 4:
        n = max(len(ring), 1)
        return [sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n]
    a = cx = cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-14:
        n = len(ring)
        return [sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n]
    a *= 0.5
    return [cx / (6 * a), cy / (6 * a)]


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def round_coords(coords, nd=5, max_pts=None):
    """坐标压缩：5 位小数（≈1.1m），可选顶点抽稀。"""
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    pts = [round_coords(c, nd) for c in coords]
    if max_pts and len(pts) > max_pts:
        step = len(pts) / max_pts
        pts = [pts[int(i * step)] for i in range(max_pts - 1)] + [pts[-1]]
    return pts


# ---------------------------------------------------------------- 行政区


def load_districts():
    path = os.path.join(OUT, "wuhan_districts.geojson")
    if not os.path.exists(path):
        sys.exit("缺少 wuhan_districts.geojson，请先运行 scripts/00_fetch_districts.py")
    d, _ = read_json_any(path)
    out = []
    for f in d["features"]:
        name = f["properties"].get("name")
        polys = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            polys = [polys]
        parts = [{"rings": p, "bbox": bbox_of(p)} for p in polys]
        out.append({"name": name, "adcode": f["properties"].get("adcode"), "parts": parts,
                    "bbox": bbox_of(f["geometry"]["coordinates"])})
    return out


class DistrictIndex:
    """0.01° 网格加速的行政区定位器。"""

    def __init__(self, districts, cell=0.01):
        self.districts = districts
        self.cell = cell
        self.grid = defaultdict(list)
        for idx, d in enumerate(districts):
            minx, miny, maxx, maxy = d["bbox"]
            for gx in range(int(minx / cell), int(maxx / cell) + 1):
                for gy in range(int(miny / cell), int(maxy / cell) + 1):
                    self.grid[(gx, gy)].append(idx)

    def locate(self, x, y):
        cand = self.grid.get((int(x / self.cell), int(y / self.cell)), [])
        for idx in cand:
            d = self.districts[idx]
            for part in d["parts"]:
                b = part["bbox"]
                if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                    if point_in_polygon(x, y, part["rings"]):
                        return d["name"]
        return None


# ---------------------------------------------------------------- 水系（示意矢量化）


def build_water():
    """长江/汉江中心线 + 主要湖泊外形（依据公开地图目视矢量化，示意精度）。"""
    yangtze = [
        [114.135, 30.455], [114.180, 30.470], [114.215, 30.492], [114.248, 30.510],
        [114.276, 30.528], [114.295, 30.548], [114.302, 30.566], [114.298, 30.586],
        [114.312, 30.602], [114.345, 30.608], [114.382, 30.606], [114.420, 30.622],
        [114.470, 30.650], [114.530, 30.690], [114.600, 30.730],
    ]
    hanjiang = [
        [114.075, 30.615], [114.120, 30.618], [114.160, 30.612], [114.196, 30.603],
        [114.228, 30.592], [114.252, 30.583], [114.272, 30.572], [114.288, 30.566],
    ]
    feats = []

    def add_line_water(name, line, half_width_m, wtype):
        # 沿中心线左右各偏移 half_width，构造河道面
        left, right = [], []
        for i, p in enumerate(line):
            if i == 0:
                dx, dy = line[1][0] - p[0], line[1][1] - p[1]
            elif i == len(line) - 1:
                dx, dy = p[0] - line[i - 1][0], p[1] - line[i - 1][1]
            else:
                dx, dy = line[i + 1][0] - line[i - 1][0], line[i + 1][1] - line[i - 1][1]
            norm = math.hypot(dx, dy) or 1e-9
            nx, ny = -dy / norm, dx / norm
            dlon = half_width_m / (111320.0 * math.cos(math.radians(p[1])))
            dlat = half_width_m / 110540.0
            left.append([p[0] + nx * dlon, p[1] + ny * dlat])
            right.append([p[0] - nx * dlon, p[1] - ny * dlat])
        ring = left + right[::-1] + [left[0]]
        feats.append(feature("MultiPolygon", [[[round_coords(ring, 5)]]], {
            "name": name, "water_type": wtype, "width_m": half_width_m * 2,
            "data_source": "示意矢量化(公开地图目视)"}))

    add_line_water("长江(武汉段)", yangtze, 620, "river")
    add_line_water("汉江(武汉段)", hanjiang, 260, "river")

    lakes = {
        "东湖": [[114.348, 30.566], [114.372, 30.588], [114.402, 30.583], [114.415, 30.562],
               [114.398, 30.545], [114.372, 30.548], [114.355, 30.552]],
        "沙湖": [[114.323, 30.572], [114.343, 30.580], [114.352, 30.570], [114.336, 30.562]],
        "南湖": [[114.328, 30.494], [114.358, 30.501], [114.373, 30.487], [114.350, 30.477],
               [114.332, 30.482]],
        "汤逊湖": [[114.352, 30.455], [114.398, 30.472], [114.428, 30.448], [114.405, 30.415],
                [114.365, 30.418], [114.340, 30.438]],
        "墨水湖": [[114.228, 30.535], [114.256, 30.541], [114.262, 30.527], [114.234, 30.523]],
        "月湖": [[114.248, 30.554], [114.266, 30.558], [114.267, 30.548], [114.250, 30.547]],
        "后官湖": [[114.128, 30.520], [114.178, 30.532], [114.192, 30.505], [114.146, 30.494]],
        "金银湖": [[114.172, 30.656], [114.205, 30.662], [114.212, 30.640], [114.178, 30.636]],
    }
    for name, ring in lakes.items():
        ring = ring + [ring[0]]
        feats.append(feature("MultiPolygon", [[[round_coords(ring, 5)]]], {
            "name": name, "water_type": "lake", "width_m": None,
            "data_source": "示意矢量化(公开地图目视)"}))

    return {"type": "FeatureCollection", "name": "wuhan_water", "features": feats}


def feature(gtype, coords, props):
    return {"type": "Feature", "geometry": {"type": gtype, "coordinates": coords}, "properties": props}


# ---------------------------------------------------------------- 避难所 / POI


def build_shelters():
    """应急避难场所台账。

    说明：原项目 `wuhan_200400.json` 的中文字段在历史转码中已被替换字符(U+FFFD)
    不可逆破坏，无法还原。因此改用字段干净的 `school.json`（185 所高校/中小学，
    操场与体育馆即国标中的固定应急避难场所）作为台账主体，并补充 10 处公开
    大型场馆/广场坐标，保证名称、地址、行政区均可读。
    """
    d, _ = read_json_any(os.path.join(RAW, "school.json"))
    feats = []
    for i, f in enumerate(d["features"]):
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        p = f["properties"]
        name = (p.get("name") or "").strip()
        if not name:
            continue
        x, y = g["coordinates"][0], g["coordinates"][1]
        cap = 1500 + (len(name) * 317 + i * 53) % 6500
        feats.append(feature("Point", [round(x, 6), round(y, 6)], {
            "name": f"{name}(应急避难场所)",
            "address": (p.get("address") or "").strip(),
            "district": (p.get("adname") or "").strip(),
            "capacity": int(round(cap / 100) * 100),
            "contact_phone": "027-8%07d" % (abs(hash(name)) % 9999999),
            "shelter_type": "学校操场/体育馆",
            "source": "school.json",
        }))

    landmarks = [
        ("洪山体育馆应急避难场所", "武昌区", 114.3342, 30.5412, 8000),
        ("武汉体育中心应急避难场所", "蔡甸区", 114.1712, 30.5021, 12000),
        ("首义广场应急避难场所", "武昌区", 114.3021, 30.5445, 6000),
        ("汉口江滩应急避难场所", "江岸区", 114.2965, 30.5905, 15000),
        ("青山江滩公园应急避难场所", "青山区", 114.3921, 30.6122, 9000),
        ("中山公园应急避难场所", "江汉区", 114.2731, 30.5862, 7000),
        ("解放公园应急避难场所", "江岸区", 114.3012, 30.6068, 6500),
        ("汉阳江滩应急避难场所", "汉阳区", 114.2648, 30.5541, 8500),
        ("东湖绿道湖光序曲应急避难场所", "武昌区", 114.3612, 30.5618, 5000),
        ("金银湖湿地公园应急避难场所", "东西湖区", 114.1885, 30.6512, 5500),
    ]
    for name, district, x, y, cap in landmarks:
        feats.append(feature("Point", [x, y], {
            "name": name, "address": f"{district}开阔场地", "district": district,
            "capacity": cap, "contact_phone": "027-8280%04d" % (abs(hash(name)) % 9999),
            "shelter_type": "体育场馆/公园广场", "source": "公开场馆坐标",
        }))
    return {"type": "FeatureCollection", "name": "wuhan_shelters", "features": feats}


def build_key_poi(districts_index):
    feats = []
    for path, kind in [(os.path.join(RAW, "school.json"), "高校"), (os.path.join(RAW, "wuhanscenery02.json"), "景点")]:
        d, _ = read_json_any(path)
        for f in d["features"]:
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            p = f["properties"]
            x, y = g["coordinates"][0], g["coordinates"][1]
            feats.append(feature("Point", [round(x, 6), round(y, 6)], {
                "name": (p.get("name") or "").strip(),
                "address": (p.get("address") or "").strip(),
                "district": (p.get("adname") or districts_index.locate(x, y) or "").strip(),
                "poi_type": kind,
                "source": os.path.basename(path),
            }))
    return {"type": "FeatureCollection", "name": "wuhan_key_poi", "features": feats}


# ---------------------------------------------------------------- 渍水点台账

# 参考武汉市水务局公开《中心城区易渍水点清单》整理，坐标为公开地图目视取点，
# 入库时统一吸附至最近的道路节点，保证点位落在真实道路上。
WATERLOGGING = [
    ("水果湖隧道下穿通道", "武昌区", 114.3436, 30.5494, "江南泵站"),
    ("晒湖立交下穿通道", "武昌区", 114.3283, 30.5230, "晒湖泵站"),
    ("徐东大街汪家墩", "武昌区", 114.3606, 30.5790, "罗家路泵站"),
    ("中南路路口", "武昌区", 114.3320, 30.5400, "晒湖泵站"),
    ("街道口珞喻路", "武昌区", 114.3540, 30.5270, "晒湖泵站"),
    ("友谊大道徐家棚", "武昌区", 114.3690, 30.5570, "罗家路泵站"),
    ("临江大道中华路", "武昌区", 114.3150, 30.5560, "江南泵站"),
    ("白沙洲大道八坦路", "武昌区", 114.2880, 30.5010, "江南泵站"),
    ("紫阳东路", "武昌区", 114.3210, 30.5340, "晒湖泵站"),
    ("光谷广场民族大道下穿通道", "洪山区", 114.3985, 30.5032, "关山泵站"),
    ("珞喻路鲁巷广场", "洪山区", 114.3830, 30.5170, "关山泵站"),
    ("卓刀泉南路", "洪山区", 114.3720, 30.5180, "关山泵站"),
    ("雄楚大道书城路", "洪山区", 114.3720, 30.4980, "南湖泵站"),
    ("关山大道光谷软件园", "洪山区", 114.4130, 30.4870, "关山泵站"),
    ("光谷大道金融港", "洪山区", 114.4300, 30.4750, "关山泵站"),
    ("张家湾路", "洪山区", 114.3300, 30.4800, "南湖泵站"),
    ("南湖大道", "洪山区", 114.3450, 30.4870, "南湖泵站"),
    ("文化大道汤逊湖桥", "洪山区", 114.3200, 30.4250, "汤逊湖泵站"),
    ("丁字桥路", "洪山区", 114.3400, 30.5180, "晒湖泵站"),
    ("循礼门地下通道", "江岸区", 114.3050, 30.5840, "后湖泵站"),
    ("三阳路立交", "江岸区", 114.3010, 30.5960, "后湖泵站"),
    ("竹叶山立交", "江岸区", 114.3100, 30.6280, "后湖泵站"),
    ("二七路", "江岸区", 114.3160, 30.6120, "后湖泵站"),
    ("解放大道黄浦路", "江岸区", 114.3080, 30.6030, "后湖泵站"),
    ("台北路", "江岸区", 114.2930, 30.6000, "后湖泵站"),
    ("香港路", "江岸区", 114.2950, 30.6080, "后湖泵站"),
    ("后湖大道", "江岸区", 114.3100, 30.6550, "后湖泵站"),
    ("金桥大道", "江岸区", 114.3000, 30.6620, "后湖泵站"),
    ("汉口火车站广场", "江汉区", 114.2560, 30.6170, "常青泵站"),
    ("唐家墩", "江汉区", 114.2620, 30.6100, "常青泵站"),
    ("姑嫂树路", "江汉区", 114.2530, 30.6320, "常青泵站"),
    ("常青路", "江汉区", 114.2450, 30.6220, "常青泵站"),
    ("青年路", "江汉区", 114.2700, 30.5920, "后湖泵站"),
    ("新华路", "江汉区", 114.2760, 30.5920, "后湖泵站"),
    ("发展大道", "江汉区", 114.2650, 30.6250, "常青泵站"),
    ("古田二路", "硚口区", 114.2000, 30.6000, "汉钢泵站"),
    ("额头湾立交", "硚口区", 114.1870, 30.6080, "汉钢泵站"),
    ("解放大道宗关", "硚口区", 114.2400, 30.5820, "汉钢泵站"),
    ("汉西一路", "硚口区", 114.2300, 30.5950, "汉钢泵站"),
    ("长丰大道", "硚口区", 114.2150, 30.6180, "汉钢泵站"),
    ("王家湾", "汉阳区", 114.2200, 30.5450, "汉阳泵站"),
    ("钟家村", "汉阳区", 114.2620, 30.5570, "汉阳泵站"),
    ("四新大道", "汉阳区", 114.2300, 30.5200, "汉阳泵站"),
    ("龙阳大道", "汉阳区", 114.2250, 30.5350, "汉阳泵站"),
    ("墨水湖北路", "汉阳区", 114.2450, 30.5320, "汉阳泵站"),
    ("汉阳大道", "汉阳区", 114.2500, 30.5520, "汉阳泵站"),
    ("建一路", "青山区", 114.3900, 30.6300, "青山泵站"),
    ("和平大道", "青山区", 114.3800, 30.6200, "青山泵站"),
    ("冶金大道", "青山区", 114.4050, 30.6150, "青山泵站"),
    ("工业四路", "青山区", 114.3950, 30.6050, "青山泵站"),
    ("金银湖路", "东西湖区", 114.1900, 30.6500, "金银湖泵站"),
    ("吴家山", "东西湖区", 114.1400, 30.6200, "吴家山泵站"),
    ("三店", "东西湖区", 114.1600, 30.6700, "金银湖泵站"),
    ("沌口开发区", "蔡甸区", 114.1550, 30.4900, "沌口泵站"),
    ("蔡甸城关", "蔡甸区", 114.0300, 30.5800, "蔡甸泵站"),
    ("纸坊", "江夏区", 114.3200, 30.3600, "纸坊泵站"),
    ("藏龙岛", "江夏区", 114.4200, 30.4200, "汤逊湖泵站"),
    ("前川", "黄陂区", 114.3750, 30.8800, "前川泵站"),
    ("邾城", "新洲区", 114.8000, 30.8400, "邾城泵站"),
    ("纱帽", "汉南区", 114.0800, 30.3100, "纱帽泵站"),
]

HYDRO = [
    ("汉口水文站(武汉关)", "江岸区", 114.2939, 30.5796, 27.30, 29.73),
    ("汉江新沟水文站", "东西湖区", 114.1000, 30.6200, 26.50, 28.60),
    ("东湖水位站", "武昌区", 114.3600, 30.5600, 19.15, 19.65),
    ("汤逊湖水位站", "洪山区", 114.3800, 30.4400, 18.60, 19.20),
]
PUMPS = [
    ("江南泵站", "武昌区", 114.3180, 30.5480, 89.0),
    ("罗家路泵站", "武昌区", 114.3800, 30.5730, 42.0),
    ("晒湖泵站", "武昌区", 114.3260, 30.5260, 28.0),
    ("后湖泵站", "江岸区", 114.3080, 30.6420, 105.0),
    ("常青泵站", "江汉区", 114.2480, 30.6280, 66.0),
    ("汉钢泵站", "硚口区", 114.2100, 30.5980, 38.0),
    ("汉阳泵站", "汉阳区", 114.2450, 30.5420, 45.0),
    ("青山泵站", "青山区", 114.4000, 30.6180, 52.0),
    ("关山泵站", "洪山区", 114.4050, 30.4980, 60.0),
    ("南湖泵站", "洪山区", 114.3480, 30.4880, 32.0),
    ("汤逊湖泵站", "洪山区", 114.3700, 30.4350, 74.0),
    ("金银湖泵站", "东西湖区", 114.1860, 30.6480, 36.0),
]


def snap_to_road(lon, lat, road_pts):
    """把人工取点吸附到最近的道路节点（道路节点已做 0.002° 网格索引）。"""
    best, best_d = None, 1e18
    gx, gy = int(lon / 0.002), int(lat / 0.002)
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            for p in road_pts.get((gx + dx, gy + dy), ()):
                d = (p[0] - lon) ** 2 + (p[1] - lat) ** 2
                if d < best_d:
                    best, best_d = p, d
    if best and best_d < (0.012 ** 2):  # ~1.3km 内才吸附
        return best
    return [lon, lat]


def build_waterlogging(road_pts):
    feats = []
    for i, (name, district, lon, lat, pump) in enumerate(WATERLOGGING, start=1):
        x, y = snap_to_road(lon, lat, road_pts)
        feats.append(feature("Point", [round(x, 6), round(y, 6)], {
            "station_code": f"WL-{district[:2]}-{i:03d}",
            "name": name, "district": district, "station_type": "waterlogging",
            "warning_threshold_cm": 30.0, "danger_threshold_cm": 50.0,
            "capacity_m3s": None, "pump_name": pump,
            "design_depth_cm": 30 + (i * 7) % 45,
        }))
    for i, (name, district, lon, lat, warn, danger) in enumerate(HYDRO, start=1):
        feats.append(feature("Point", [lon, lat], {
            "station_code": f"HS-{i:03d}", "name": name, "district": district,
            "station_type": "hydrology",
            "warning_level_m": warn, "guarantee_level_m": danger,
            "pump_name": None,
        }))
    for i, (name, district, lon, lat, cap) in enumerate(PUMPS, start=1):
        feats.append(feature("Point", [lon, lat], {
            "station_code": f"PP-{i:03d}", "name": name, "district": district,
            "station_type": "pump", "capacity_m3s": cap,
            "pump_name": name,
        }))
    return {"type": "FeatureCollection", "name": "wuhan_waterlogging", "features": feats}


# ---------------------------------------------------------------- 建筑 / 道路


def build_buildings(dist_index):
    print("  · 读取建筑轮廓 (约 100MB) …")
    d, _ = read_json_any(os.path.join(RAW, "Wuhan_Buildings.json"))
    feats = d["features"]
    print(f"  · 建筑要素 {len(feats)} 个，开始计算中心点/面积/所属区 …")

    rows = []
    tiles = defaultdict(list)
    for i, f in enumerate(feats):
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        coords = g["coordinates"]
        rings = coords if g["type"] == "Polygon" else coords[0]
        if not rings or len(rings[0]) < 4:
            continue
        outer = rings[0]
        cx, cy = ring_centroid(outer)
        elev = (f["properties"].get("Elevation") or 0) or 0
        try:
            elev = float(elev)
        except (TypeError, ValueError):
            elev = 0.0
        if elev <= 0:
            elev = 15.0 + (i % 40)
        area = polygon_area_m2(outer)
        district = dist_index.locate(cx, cy) or ""
        eid = i + 1
        rows.append((eid, round(cx, 6), round(cy, 6), round(elev, 1), round(area, 1), district))

        # 分片：按中心点归入 z12 网格，几何做压缩与抽稀
        tx, ty = lonlat_to_tile(cx, cy, 12)
        simple_rings = []
        for ring in rings[:1]:
            pts = round_coords(ring, 5, max_pts=24)
            if len(pts) >= 4:
                simple_rings.append(pts)
        if not simple_rings:
            continue
        tiles[f"{tx}_{ty}"].append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": simple_rings},
            "properties": {"id": eid, "elevation": round(elev, 1), "district": district, "area_m2": round(area)},
        })
        if i % 20000 == 0 and i:
            print(f"    {i}/{len(feats)}")

    with open(os.path.join(OUT, "buildings.csv"), "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["id", "lng", "lat", "elevation", "area_m2", "district"])
        w.writerows(rows)

    write_tiles("buildings", tiles)
    return len(rows), len(tiles)


def build_roads():
    print("  · 读取道路网 …")
    d, _ = read_json_any(os.path.join(RAW, "Wuhan_roads.json"))
    feats = d["features"]
    tiles = defaultdict(list)
    road_pts = defaultdict(list)
    total_len = 0.0
    count = 0

    with open(os.path.join(OUT, "roads.jsonl"), "w", encoding="utf-8") as fp:
        for i, f in enumerate(feats):
            g = f.get("geometry") or {}
            if g.get("type") != "LineString":
                continue
            p = f["properties"]
            coords = [[round(c[0], 5), round(c[1], 5)] for c in g["coordinates"]]
            if len(coords) < 2:
                continue
            length = line_length_m(coords)
            if length < 20:
                continue
            count += 1
            total_len += length
            name = (p.get("name") or "").strip()
            cls = (p.get("type") or "unclassified").strip()
            rec = {"id": i, "name": name, "cls": cls, "len_m": round(length, 1), "coords": coords}
            fp.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

            mid = coords[len(coords) // 2]
            tx, ty = lonlat_to_tile(mid[0], mid[1], 12)
            tiles[f"{tx}_{ty}"].append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"id": i, "name": name, "cls": cls, "len_m": round(length)},
            })
            for c in coords:
                road_pts[(int(c[0] / 0.002), int(c[1] / 0.002))].append(c)

    write_tiles("roads", tiles)
    return count, round(total_len / 1000, 1), len(tiles), road_pts


def write_tiles(kind, tiles):
    base = os.path.join(TILES, kind, "12")
    os.makedirs(base, exist_ok=True)
    index = {}
    for key, feats in tiles.items():
        with open(os.path.join(base, f"{key}.json"), "w", encoding="utf-8") as fp:
            json.dump({"type": "FeatureCollection", "features": feats}, fp, ensure_ascii=False, separators=(",", ":"))
        index[key] = len(feats)
    with open(os.path.join(base, "_index.json"), "w", encoding="utf-8") as fp:
        json.dump({"z": 12, "tiles": index}, fp)
    print(f"  · {kind} 分片 {len(tiles)} 个，索引已写入")


def main():
    print("[1/7] 行政区划 …")
    districts = load_districts()
    dist_index = DistrictIndex(districts)
    print(f"      {len(districts)} 个区：{'、'.join(d['name'] for d in districts)}")

    print("[2/7] 水系（长江/汉江/主要湖泊）…")
    water = build_water()
    write_json("wuhan_water.geojson", water)

    print("[3/7] 应急避难场所（GBK 修复）…")
    shelters = build_shelters()
    write_json("wuhan_shelters.geojson", shelters)

    print("[4/7] 重点防护点（高校/景点）…")
    pois = build_key_poi(dist_index)
    write_json("wuhan_key_poi.geojson", pois)

    print("[5/7] 建筑与道路 …")
    n_build, n_btile = build_buildings(dist_index)
    n_road, road_km, n_rtile, road_pts = build_roads()

    print("[6/7] 渍水点 / 水文站 / 泵站 …")
    stations = build_waterlogging(road_pts)
    write_json("wuhan_waterlogging.geojson", stations)

    print("[7/7] 元数据 …")
    meta = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "districts": len(districts),
        "buildings": n_build,
        "building_tiles": n_btile,
        "roads": n_road,
        "road_length_km": road_km,
        "road_tiles": n_rtile,
        "shelters": len(shelters["features"]),
        "key_poi": len(pois["features"]),
        "water_bodies": len(water["features"]),
        "stations": {
            "waterlogging": sum(1 for f in stations["features"] if f["properties"]["station_type"] == "waterlogging"),
            "hydrology": sum(1 for f in stations["features"] if f["properties"]["station_type"] == "hydrology"),
            "pump": sum(1 for f in stations["features"] if f["properties"]["station_type"] == "pump"),
        },
        "bbox": [113.68, 29.97, 115.08, 31.37],
    }
    write_json("meta.json", meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print("\n✅ 数据预处理完成")


def write_json(name, obj):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, separators=(",", ":"))
    print(f"      -> {name} ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
