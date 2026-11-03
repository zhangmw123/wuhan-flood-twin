"""几何引擎单元测试（标准库 unittest，零依赖可跑）。

运行： python -m unittest discover -s backend/tests -v
覆盖：球面距离 / 点面判断（含洞）/ 圆缓冲面积 / 线段裁剪 /
      网格索引 / A* 最短路与避障降级 / 瓦片编号
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.core import geo


class TestDistance(unittest.TestCase):
    def test_haversine_known_value(self):
        """武汉纬线上 0.1° 经度 ≈ 9.59 km（111.32*cos(30.5)/10）。"""
        d = geo.haversine((114.30, 30.50), (114.40, 30.50))
        self.assertAlmostEqual(d, 9_590, delta=100)

    def test_haversine_zero_and_symmetry(self):
        self.assertEqual(geo.haversine((114.3, 30.5), (114.3, 30.5)), 0.0)
        a = geo.haversine((114.30, 30.50), (114.40, 30.60))
        b = geo.haversine((114.40, 30.60), (114.30, 30.50))
        self.assertAlmostEqual(a, b, places=6)

    def test_haversine_antimeridian(self):
        """跨 180° 经线不应算出绕地球大半圈的距离。"""
        d = geo.haversine((179.99, 0.0), (-179.99, 0.0))
        self.assertLess(d, 3_000)


class TestPointInPolygon(unittest.TestCase):
    square = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [0.0, 0.0]]

    def test_inside(self):
        self.assertTrue(geo.point_in_ring(2.0, 2.0, self.square))

    def test_outside(self):
        self.assertFalse(geo.point_in_ring(5.0, 2.0, self.square))
        self.assertFalse(geo.point_in_ring(-1.0, 2.0, self.square))

    def test_hole_is_excluded(self):
        """带洞多边形：洞内点应判为不在面内。"""
        hole = [[1.5, 1.5], [2.5, 1.5], [2.5, 2.5], [1.5, 2.5], [1.5, 1.5]]
        self.assertFalse(geo.point_in_polygon(2.0, 2.0, [self.square, hole]))
        self.assertTrue(geo.point_in_polygon(0.5, 0.5, [self.square, hole]))

    def test_degenerate_ring(self):
        self.assertFalse(geo.point_in_ring(0.0, 0.0, [[0.0, 0.0], [1.0, 1.0]]))


class TestCircleAndArea(unittest.TestCase):
    def test_circle_area_accuracy(self):
        """半径 500m 的 48 边形圆，面积应与 πr² 相差 < 2%。"""
        ring = geo.circle_ring(114.30, 30.50, 500)
        area = geo.ring_area_m2(ring)
        self.assertAlmostEqual(area, math.pi * 500 ** 2, delta=math.pi * 500 ** 2 * 0.02)

    def test_circle_area_scales_quadratically(self):
        a1 = geo.ring_area_m2(geo.circle_ring(114.30, 30.50, 500))
        a2 = geo.ring_area_m2(geo.circle_ring(114.30, 30.50, 1000))
        self.assertAlmostEqual(a2 / a1, 4.0, delta=0.15)

    def test_circle_ring_closed(self):
        ring = geo.circle_ring(114.30, 30.50, 300, segments=16)
        self.assertEqual(len(ring), 17)
        self.assertEqual(ring[0], ring[-1])


class TestLineInPolygon(unittest.TestCase):
    """裁剪长度以米为单位：0.002° 经度在 30.5°N 约 192m。"""

    square = [
        [114.299, 30.499], [114.301, 30.499], [114.301, 30.501],
        [114.299, 30.501], [114.299, 30.499],
    ]
    bbox = [114.299, 30.499, 114.301, 30.501]

    def test_horizontal_segment_clipped(self):
        length = geo.segment_in_polygon_len((114.298, 30.500), (114.302, 30.500), [self.square])
        # 贯穿宽度 ≈ 0.002° × 95.9km/° ≈ 192m
        self.assertAlmostEqual(length, 192, delta=30)

    def test_segment_fully_outside(self):
        length = geo.segment_in_polygon_len((114.298, 30.510), (114.302, 30.510), [self.square])
        self.assertLess(length, 1.0)

    def test_segment_fully_inside_is_full_length(self):
        a, b = (114.2995, 30.500), (114.3005, 30.500)
        expected = geo.haversine(a, b)
        length = geo.segment_in_polygon_len(a, b, [self.square])
        self.assertAlmostEqual(length, expected, delta=2)

    def test_line_length_is_sum_of_segments(self):
        coords = [(114.298, 30.500), (114.302, 30.500)]
        total = geo.line_in_polygon_len(coords, [self.square], self.bbox)
        self.assertAlmostEqual(total, 192, delta=30)

    def test_line_outside_bbox_returns_zero(self):
        coords = [(115.000, 31.000), (115.001, 31.001)]
        self.assertEqual(geo.line_in_polygon_len(coords, [self.square], self.bbox), 0.0)


class TestGridIndex(unittest.TestCase):
    def test_query_matches_bruteforce(self):
        """网格粗筛结果必须是暴力筛选结果的全集（允许粗筛多给，不能漏）。"""
        pts = []
        for i in range(20):
            for j in range(20):
                pts.append({"id": f"{i}-{j}", "lng": 114.0 + i * 0.001, "lat": 30.5 + j * 0.001})
        idx = geo.GridIndex(cell=0.005)
        for p in pts:
            idx.insert(p, p["lng"], p["lat"])
        box = [114.005, 30.505, 114.012, 30.512]
        got = {p["id"] for p in idx.query_bbox(box)}
        want = {
            p["id"] for p in pts
            if box[0] <= p["lng"] <= box[2] and box[1] <= p["lat"] <= box[3]
        }
        self.assertTrue(want, "暴力筛选不应为空")
        self.assertTrue(want.issubset(got), "粗筛漏点即索引错误")
        # 网格边长 0.005，外扩不应超过 2 个 cell 的边界带
        self.assertLess(len(got) - len(want), 200)

    def test_insert_bbox_covers_cells(self):
        idx = geo.GridIndex(cell=0.01)
        idx.insert_bbox({"id": "line"}, [0.001, 0.001, 0.019, 0.019])
        hit = idx.query_bbox([0.004, 0.004, 0.006, 0.006])
        self.assertEqual([o["id"] for o in hit], ["line"])

    def test_empty_index(self):
        self.assertEqual(geo.GridIndex(cell=0.01).query_bbox([0, 0, 1, 1]), [])


class TestAStar(unittest.TestCase):
    def setUp(self):
        # 0-1-2-3 直线，附带 0-3 直连边 rid="J"
        self.nodes = {0: (0.0, 0.0), 1: (0.001, 0.0), 2: (0.002, 0.0), 3: (0.003, 0.0)}
        self.adj = {
            0: [(1, 100.0, "A"), (3, 300.0, "J")],
            1: [(0, 100.0, "A"), (2, 100.0, "B")],
            2: [(1, 100.0, "B"), (3, 100.0, "C")],
            3: [(2, 100.0, "C"), (0, 300.0, "J")],
        }

    def test_shortest_path(self):
        path, dist = geo.astar(self.nodes, self.adj, 0, 3)
        self.assertIsNotNone(path)
        self.assertAlmostEqual(dist, 300.0, delta=1.0)
        # astar 返回坐标序列，首尾须对应起终点
        self.assertEqual(path[0], self.nodes[0])
        self.assertEqual(path[-1], self.nodes[3])

    def test_blocked_road_forces_detour(self):
        """封掉 A 段后必须走 J（300m），距离变大。"""
        path, dist = geo.astar(self.nodes, self.adj, 0, 3, blocked_roads={"A"})
        self.assertIsNotNone(path)
        self.assertAlmostEqual(dist, 300.0, delta=1.0)
        self.assertEqual(path, [self.nodes[0], self.nodes[3]], "封路后应直接走 J 段")

    def test_hard_block_unreachable(self):
        """同时封 A 与 J 时硬禁行模式应返回不可达。"""
        path, dist = geo.astar(self.nodes, self.adj, 0, 3, blocked_roads={"A", "J"})
        self.assertIsNone(path)

    def test_penalty_degrade_passable(self):
        """降级通行模式：封路仍可走，但代价被放大。"""
        _, hard = geo.astar(self.nodes, self.adj, 0, 3, blocked_roads={"A"}, penalty=1.0)
        path2, soft = geo.astar(self.nodes, self.adj, 0, 3, blocked_roads={"A", "J"}, penalty=10.0)
        self.assertIsNotNone(path2)
        self.assertGreater(soft, hard * 3)

    def test_unreachable_returns_none(self):
        adj = {0: [], 1: []}
        path, dist = geo.astar({0: (0.0, 0.0), 1: (0.001, 0.0)}, adj, 0, 1)
        self.assertIsNone(path)


class TestTiles(unittest.TestCase):
    def test_tile_of_wuhan(self):
        """武汉 (114.30, 30.50) 在 z12 的瓦片：x=3348, y=1683（4096 网格）。"""
        x, y = geo.lonlat_to_tile(114.30, 30.50, 12)
        self.assertAlmostEqual(x, 3348, delta=3)
        self.assertAlmostEqual(y, 1683, delta=3)

    def test_tile_bbox_contains_point(self):
        x, y = geo.lonlat_to_tile(114.30, 30.50, 12)
        tb = geo.tile_bbox(12, x, y)
        self.assertLessEqual(tb[0], 114.30)
        self.assertGreaterEqual(tb[2], 114.30)
        self.assertLessEqual(tb[1], 30.50)
        self.assertGreaterEqual(tb[3], 30.50)

    def test_tiles_for_bbox_covers_range(self):
        tiles = geo.tiles_for_bbox([114.300, 30.500, 114.301, 30.501], 12)
        self.assertGreaterEqual(len(tiles), 1)
        self.assertLessEqual(len(tiles), 4)
        self.assertTrue(all(t[0] == 12 for t in tiles))

    def test_zoom_increases_tile_count(self):
        """同一 bbox 在更高层级会落到更多瓦片：单调性校验。"""
        bbox = [114.20, 30.40, 114.45, 30.65]
        n10 = len(geo.tiles_for_bbox(bbox, 10))
        n12 = len(geo.tiles_for_bbox(bbox, 12))
        self.assertGreater(n12, n10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
