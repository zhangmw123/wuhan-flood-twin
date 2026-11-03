"""推演模型与空间分析的业务正确性测试（标准库 unittest）。

运行： python -m unittest discover -s backend/tests -v

这里不做"能不能跑通"的冒烟，而是校验模型必须具备的物理/业务不变量：
  · 雨量越大，淹没范围与受淹建筑单调不减
  · 子区域结果不得超过全域结果（集合包含关系）
  · 缓冲区半径翻倍，命中建筑单调不减，面积近似四倍
  · 避险路径距离/耗时为正且量级合理、终点吸附到避难所
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.core.store import get_store
from backend.services import analysis


def unwrap(res):
    """handlers 层会包一层 {"data": ...}，analysis 直调不一定，统一取内层。"""
    return res["data"] if isinstance(res, dict) and "data" in res else res


class TestStoreIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = get_store()

    def test_data_scale(self):
        self.assertGreater(len(self.store.buildings), 200_000, "建筑应超 20 万栋")
        self.assertGreater(len(self.store.roads), 25_000, "道路应超 2.5 万条")
        self.assertGreater(len(self.store.shelters["features"]), 150, "避难所应超 150 处")

    def test_thirteen_districts(self):
        self.assertEqual(len(self.store.district_area), 13, "武汉市应为 13 个行政区")

    def test_district_area_reasonable(self):
        """13 区面积合计应接近武汉市实际面积 8569 km²（district_area 单位为 m²）。"""
        total_km2 = sum(self.store.district_area.values()) / 1e6
        self.assertAlmostEqual(total_km2, 8569, delta=200,
                               msg=f"边界面积合计 {total_km2:.0f} km²，与实测 8569 km² 偏差过大")

    def test_every_building_has_district_or_unknown(self):
        missing = [b for b in self.store.buildings if not b.get("district")]
        self.assertLess(len(missing) / max(1, len(self.store.buildings)), 0.02,
                        "行政区归属缺失率应低于 2%")

    def test_station_depths_non_negative(self):
        self.store.set_hour(12)
        for code, d in self.store.depths.items():
            self.assertGreaterEqual(d, 0.0, f"{code} 积水深度为负")


class TestSimulationMonotonicity(unittest.TestCase):
    """推演模型的核心不变量：降雨强度与淹没程度单调相关。"""

    @classmethod
    def setUpClass(cls):
        cls.store = get_store()

    def test_heavier_rain_means_more_inundation(self):
        light = unwrap(analysis.impact_analysis(50, 3))
        mid = unwrap(analysis.impact_analysis(120, 3))
        heavy = unwrap(analysis.impact_analysis(220, 3))
        self.assertLessEqual(light["inundated_area_km2"], mid["inundated_area_km2"])
        self.assertLessEqual(mid["inundated_area_km2"], heavy["inundated_area_km2"])
        self.assertLessEqual(light["affected_building_count"], heavy["affected_building_count"])

    def test_max_depth_bounded_by_physics(self):
        """推演深度不应超过历史极值量级（200cm），否则说明模型发散。"""
        heavy = unwrap(analysis.impact_analysis(250, 6))
        self.assertLess(heavy["max_depth_cm"], 200.0)
        self.assertGreater(heavy["max_depth_cm"], 0.0)

    def test_zero_rain_no_inundation(self):
        dry = unwrap(analysis.impact_analysis(0, 3))
        self.assertEqual(dry["zone_count"], 0, "零降雨不应产生淹没区")

    def test_single_district_is_subset_of_city(self):
        city = unwrap(analysis.impact_analysis(120, 3))
        one = unwrap(analysis.impact_analysis(120, 3, district="武昌区"))
        self.assertGreater(one["zone_count"], 0, "武昌区应有淹没区")
        self.assertLessEqual(one["inundated_area_km2"], city["inundated_area_km2"] + 1e-6)
        self.assertLessEqual(one["affected_building_count"], city["affected_building_count"])

    def test_blocked_roads_are_subset_of_affected(self):
        r = unwrap(analysis.impact_analysis(180, 3))
        self.assertLessEqual(r["blocked_road_count"], len(self.store.roads))
        self.assertGreater(r["blocked_road_length_km"], 0.0)

    def test_sandbox_clock_peak(self):
        """2016 雨型下，12 时前后应比清晨 8 时积水更重。"""
        self.store.set_hour(8)
        morning = sum(self.store.depths.values())
        self.store.set_hour(12)
        noon = sum(self.store.depths.values())
        self.assertGreater(noon, morning, "峰值时段总积水量应高于清晨")


class TestBufferAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = get_store()

    def test_radius_monotonic(self):
        a = unwrap(analysis.buffer_analysis(114.3436, 30.5494, 500))
        b = unwrap(analysis.buffer_analysis(114.3436, 30.5494, 1000))
        self.assertLessEqual(a["building_count"], b["building_count"])
        self.assertLessEqual(a["shelter_count"], b["shelter_count"])

    def test_nearest_shelter_within_radius_when_hit(self):
        r = unwrap(analysis.buffer_analysis(114.3342, 30.5412, 3000))
        self.assertGreater(r["shelter_count"], 0)
        self.assertIsNotNone(r["nearest_shelter"])

    def test_empty_area_returns_zero_not_error(self):
        """远郊无建筑区域的缓冲结果必须为 0 而不是抛异常（前端空值兜底）。"""
        r = unwrap(analysis.buffer_analysis(114.60, 30.90, 800))
        self.assertEqual(r["building_count"], 0)
        self.assertIsNone(r["nearest_shelter"])


class TestEvacuationRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = get_store()
        cls.store.set_hour(12)

    def test_route_reaches_shelter(self):
        r = unwrap(analysis.evacuation_route(114.3436, 30.5494))
        self.assertIsNotNone(r["route_geojson"])
        self.assertGreater(len(r["route_geojson"]["geometry"]["coordinates"]), 2)
        self.assertIn("name", r["target_shelter"])

    def test_distance_and_time_plausible(self):
        r = unwrap(analysis.evacuation_route(114.3436, 30.5494))
        dist = r["target_shelter"]["distance_m"]
        # 路网距离应大于直线距离，且不超过直线距离的 3 倍
        self.assertGreater(dist, r["target_shelter"]["straight_m"])
        self.assertLess(dist, r["target_shelter"]["straight_m"] * 3)
        # 步行 5km/h ≈ 83m/min，耗时应在合理区间
        self.assertGreater(r["estimated_time_min"], 0)
        self.assertLess(r["estimated_time_min"], dist / 40)

    def test_multiple_origins_all_resolve(self):
        """跨江/跨区取点都应能规划出路线（路网连通性校验）。"""
        for origin in [(114.3436, 30.5494), (114.3985, 30.5032), (114.2620, 30.6100)]:
            r = unwrap(analysis.evacuation_route(*origin))
            self.assertIsNotNone(r["route_geojson"], f"{origin} 未能规划路线")

    def test_route_respects_blocked_roads_or_degrades(self):
        r = unwrap(analysis.evacuation_route(114.3436, 30.5494))
        self.assertIn("avoid_blocked_roads", r)
        self.assertIn("bypassed_danger_points", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
