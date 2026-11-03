"""纯 Python 几何引擎（零第三方依赖）。

为「本地零依赖模式」提供与 PostGIS 等价的空间运算能力：
点面判断、圆缓冲面构造、线面裁剪与长度统计、最近邻检索、A* 路网寻路。
所有坐标均为 WGS84 经纬度 (lng, lat)，距离计算使用 haversine。
"""
import heapq
import math

EARTH_R = 6371008.8


# ------------------------------------------------------------------ 基础


def haversine(a, b):
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(min(1.0, h)))


def bbox_of(coords, acc=None):
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


def bbox_intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def bbox_contains(b, x, y):
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    if not rings or not point_in_ring(x, y, rings[0]):
        return False
    for hole in rings[1:]:
        if point_in_ring(x, y, hole):
            return False
    return True


def circle_ring(lng, lat, radius_m, segments=48):
    """以 (lng,lat) 为圆心构造近似圆多边形环（经纬度局部投影）。"""
    ring = []
    dlat = radius_m / 110540.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)) or 1e-6)
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        ring.append([round(lng + dlon * math.cos(a), 6), round(lat + dlat * math.sin(a), 6)])
    return ring


def ring_area_m2(ring):
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


# ------------------------------------------------------------------ 线面


def _seg_intersect(p1, p2, p3, p4):
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1 = ccw(p3, p4, p1)
    d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3)
    d4 = ccw(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def segment_in_polygon_len(a, b, rings):
    """返回线段落在多边形内部的长度(m)：采样端点 + 与边界交点。"""
    step = 12.0  # 采样间距(m)
    seg_len = haversine(a, b)
    n = max(2, int(seg_len / step))
    inside = 0
    for i in range(n):
        t = (i + 0.5) / n
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        if point_in_polygon(x, y, rings):
            inside += 1
    return seg_len * inside / n


def line_in_polygon_len(coords, rings, bbox):
    """折线在面内的总长度(m)。bbox 为面的外包框，用于快速剔除。"""
    if not bbox_intersects(bbox_of(coords), bbox):
        return 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        total += segment_in_polygon_len(coords[i], coords[i + 1], rings)
    return total


# ------------------------------------------------------------------ 索引


class GridIndex:
    """均匀网格空间索引：cell 度数为单位，用于点/线对象的粗筛。"""

    def __init__(self, cell=0.005):
        self.cell = cell
        self.grid = {}

    def key(self, x, y):
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def insert(self, obj, x, y):
        self.grid.setdefault(self.key(x, y), []).append(obj)

    def insert_bbox(self, obj, b):
        for gx in range(int(math.floor(b[0] / self.cell)), int(math.floor(b[2] / self.cell)) + 1):
            for gy in range(int(math.floor(b[1] / self.cell)), int(math.floor(b[3] / self.cell)) + 1):
                self.grid.setdefault((gx, gy), []).append(obj)

    def query_bbox(self, b):
        seen = set()
        out = []
        for gx in range(int(math.floor(b[0] / self.cell)), int(math.floor(b[2] / self.cell)) + 1):
            for gy in range(int(math.floor(b[1] / self.cell)), int(math.floor(b[3] / self.cell)) + 1):
                for obj in self.grid.get((gx, gy), ()):  # noqa: E501
                    oid = id(obj)
                    if oid in seen:
                        continue
                    seen.add(oid)
                    out.append(obj)
        return out


# ------------------------------------------------------------------ 寻路


def build_graph(roads, bbox, cell=0.0008):
    """由道路折线构建无向图：节点为 5 位小数的道路节点。

    返回 (nodes, adj)。adj[node] = [(neighbor, weight_m, road_id)]
    """
    nodes = {}
    adj = {}
    minx, miny, maxx, maxy = bbox
    for road in roads:
        coords = road["coords"]
        prev = None
        for c in coords:
            if not (minx <= c[0] <= maxx and miny <= c[1] <= maxy):
                prev = None
                continue
            key = (round(c[0], 5), round(c[1], 5))
            if key not in nodes:
                nodes[key] = c
                adj[key] = []
            if prev is not None:
                w = haversine(prev, key)
                if w > 0:
                    adj[prev].append((key, w, road["id"]))
                    adj[key].append((prev, w, road["id"]))
            prev = key
    return nodes, adj


def nearest_node(nodes, lng, lat):
    best, bd = None, 1e18
    for key, c in nodes.items():
        d = (c[0] - lng) ** 2 + (c[1] - lat) ** 2
        if d < bd:
            bd, best = d, key
    return best


def astar(nodes, adj, start, goal, blocked_roads=frozenset(), penalty=1.0):
    """A* 最短路。

    blocked_roads 为积水阻断道路 id 集合；penalty=1.0 表示硬禁行，
    penalty>1 表示"降级通行"（应急车辆可涉水通过，但代价放大）。
    """
    if start is None or goal is None or start not in adj or goal not in adj:
        return None, 0.0

    def h(n):
        return haversine(nodes[n], nodes[goal])

    openq = [(h(start), 0.0, start)]
    came = {}
    gscore = {start: 0.0}
    closed = set()
    while openq:
        _, g, cur = heapq.heappop(openq)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return [nodes[k] for k in path], g
        if cur in closed:
            continue
        closed.add(cur)
        for nxt, w, rid in adj[cur]:
            if rid in blocked_roads:
                if penalty <= 1.0:
                    continue
                w = w * penalty
            ng = g + w
            if ng < gscore.get(nxt, 1e18):
                gscore[nxt] = ng
                came[nxt] = cur
                heapq.heappush(openq, (ng + h(nxt), ng, nxt))
    return None, 0.0


def simplify_path(coords, tolerance_m=8.0):
    """Douglas-Peucker 抽稀，减少前端渲染顶点。"""
    if len(coords) < 3:
        return coords
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        s, e = stack.pop()
        if e - s < 2:
            continue
        maxd, idx = -1.0, -1
        a, b = coords[s], coords[e]
        ab = haversine(a, b) or 1e-9
        for i in range(s + 1, e):
            p = coords[i]
            # 点到线段距离（平面近似）
            t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) /
                              (((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) or 1e-18)))
            cx, cy = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            d = haversine(p, [cx, cy])
            if d > maxd:
                maxd, idx = d, i
        if maxd > tolerance_m and idx > 0:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    out = [c for c, k in zip(coords, keep) if k]
    return out if len(out) >= 2 else coords


# ------------------------------------------------------------------ 瓦片


def tile_bbox(z, x, y):
    n = 2 ** z
    lon1 = x / n * 360.0 - 180.0
    lon2 = (x + 1) / n * 360.0 - 180.0
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return [lon1, lat2, lon2, lat1]


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_for_bbox(bbox, z):
    x1, y1 = lonlat_to_tile(bbox[0], bbox[3], z)
    x2, y2 = lonlat_to_tile(bbox[2], bbox[1], z)
    return [(z, x, y) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)]
