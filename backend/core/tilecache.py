"""瓦片内存缓存（LRU + 条数预算）。

背景：z12 建筑瓦片单个 7MB、2.6 万栋，`json.load` 一次约 1.5~2s。
零依赖模式下每个视域请求都重新读盘解析，8 并发时 P95 直接飙到 10s。

策略：
  · 缓存「已解析的要素 + 预计算外包框」，避免重复解析与重复算 bbox
  · 按要素总条数做预算淘汰（比按瓦片数更贴近真实内存占用）
  · 懒加载：只有被请求到的瓦片才进缓存，冷门区不占内存
  · 命中率暴露给 /health，便于观察（可观测性）

生产环境走 Martin/MVT，不经过这层；此处只服务零依赖单机模式。
"""
import json
import os
import threading
from collections import OrderedDict


class TileCache:
    def __init__(self, max_features=120_000, max_tiles=48):
        self.max_features = max_features
        self.max_tiles = max_tiles
        self._data = OrderedDict()      # key -> (items, feature_count)
        self._features = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return hit[0]

    def put(self, key, items):
        n = len(items)
        if n > self.max_features:       # 单瓦片已超预算，不缓存，避免抖动
            return
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return
            self._data[key] = (items, n)
            self._features += n
            while self._data and (self._features > self.max_features
                                  or len(self._data) > self.max_tiles):
                _, (old_items, old_n) = self._data.popitem(last=False)
                self._features -= old_n

    def stats(self):
        with self._lock:
            total = self.hits + self.misses
            return {
                "tiles_cached": len(self._data),
                "features_cached": self._features,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "max_features": self.max_features,
            }

    def clear(self):
        with self._lock:
            self._data.clear()
            self._features = 0


TILE_CACHE = TileCache()


def load_tile(tile_dir, kind, z, x, y):
    """加载瓦片，返回 [(feature, bbox), ...]；文件不存在返回空列表。"""
    key = (kind, z, x, y)
    cached = TILE_CACHE.get(key)
    if cached is not None:
        return cached

    path = os.path.join(tile_dir, kind, str(z), f"{x}_{y}.json")
    if not os.path.exists(path):
        TILE_CACHE.put(key, [])
        return []

    with open(path, "r", encoding="utf-8") as fp:
        fc = json.load(fp)

    items = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        coords = g.get("coordinates")
        if not coords:
            continue
        b = _bbox(coords)
        items.append((f, b))
    TILE_CACHE.put(key, items)
    return items


def _bbox(coords):
    """递归求坐标外包框（坐标嵌套层级固定，手写展开比通用递归快）。"""
    if not coords:
        return [1e9, 1e9, -1e9, -1e9]
    if isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        return [x, y, x, y]
    minx = miny = 1e9
    maxx = maxy = -1e9
    for c in coords:
        if c and isinstance(c[0], (int, float)):
            x, y = c[0], c[1]
            if x < minx: minx = x
            if y < miny: miny = y
            if x > maxx: maxx = x
            if y > maxy: maxy = y
        else:
            b = _bbox(c)
            if b[0] < minx: minx = b[0]
            if b[1] < miny: miny = b[1]
            if b[2] > maxx: maxx = b[2]
            if b[3] > maxy: maxy = b[3]
    return [minx, miny, maxx, maxy]
