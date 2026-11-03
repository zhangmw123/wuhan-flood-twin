<template>
  <aside class="right">
    <section class="panel station-panel">
      <div class="panel-head">
        <span class="title">重点渍水点实时监测</span>
        <span class="chip" :class="store.alertStations.length ? 'danger' : 'ok'">
          {{ store.alertStations.length }} / {{ stationTotal }} 超警
        </span>
      </div>
      <div class="panel-body list">
        <div
          v-for="(s, i) in store.topStations"
          :key="s.properties.station_code"
          class="row"
          @click="focusStation(s)"
        >
          <span class="rank mono" :class="levelClass(s.properties.alert_level)">{{ String(i + 1).padStart(2, '0') }}</span>
          <div class="row-main">
            <div class="row-top">
              <span class="name">{{ s.properties.name }}</span>
              <span class="depth mono" :class="levelClass(s.properties.alert_level)">
                {{ (s.properties.current_depth_cm ?? 0).toFixed(1) }}<small>cm</small>
              </span>
            </div>
            <div class="bar">
              <i
                :style="{ width: barWidth(s.properties.current_depth_cm || 0) }"
                :class="levelClass(s.properties.alert_level)"
              ></i>
            </div>
            <div class="row-bottom">
              <span class="muted">{{ s.properties.district }}</span>
              <span class="muted">{{ s.properties.pump_name || '—' }}</span>
            </div>
          </div>
        </div>
        <div v-if="!store.topStations.length" class="empty">暂无站点数据</div>
      </div>
    </section>

    <section class="panel impact-panel">
      <div class="panel-head">
        <span class="title">内涝灾情评估</span>
        <button class="btn" :disabled="store.impactLoading" @click="store.runImpact()">
          {{ store.impactLoading ? '计算中…' : store.impact ? '重新评估' : '生成报告' }}
        </button>
      </div>
      <div class="panel-body">
        <template v-if="store.impact">
          <div class="impact-grid">
            <div class="mini">
              <span class="k">淹没面积</span>
              <span class="v mono">{{ store.impact.inundated_area_km2 }}<small>km²</small></span>
            </div>
            <div class="mini">
              <span class="k">受淹建筑</span>
              <span class="v mono danger">{{ store.impact.affected_building_count }}<small>栋</small></span>
            </div>
            <div class="mini">
              <span class="k">阻断道路</span>
              <span class="v mono danger">{{ store.impact.blocked_road_count }}<small>条</small></span>
            </div>
            <div class="mini">
              <span class="k">阻断里程</span>
              <span class="v mono">{{ store.impact.blocked_road_length_km }}<small>km</small></span>
            </div>
            <div class="mini">
              <span class="k">威胁重点单位</span>
              <span class="v mono">{{ store.impact.threatened_key_poi_count }}<small>处</small></span>
            </div>
            <div class="mini">
              <span class="k">受威胁避难所</span>
              <span class="v mono warn">{{ store.impact.threatened_shelter_count }}<small>处</small></span>
            </div>
          </div>
          <div class="impact-meta">
            <span class="chip info">情景 {{ store.impact.query.rainfall_mm }}mm / {{ store.impact.query.duration_hours }}h</span>
            <span class="chip info">{{ store.impact.zone_count }} 个淹没区</span>
            <span class="chip" :class="engineClass">{{ store.impact.engine === 'postgis' ? 'PostGIS' : 'Python 几何引擎' }}</span>
            <span class="muted mono">耗时 {{ store.impact.compute_ms }}ms</span>
          </div>
          <div class="breakdown" v-if="store.impact.district_breakdown?.length">
            <div class="bd-title">受淹建筑行政区分布</div>
            <div v-for="d in store.impact.district_breakdown.slice(0, 5)" :key="d.district" class="bd-row">
              <span>{{ d.district }}</span>
              <div class="bd-bar"><i :style="{ width: bdWidth(d.buildings) }"></i></div>
              <span class="mono">{{ d.buildings }}</span>
            </div>
          </div>
        </template>
        <div v-else class="empty">
          点击「生成报告」，按当前雨量情景推演淹没范围，并经空间叠加计算受淹建筑、阻断道路与受影响重点单位。
        </div>
      </div>
    </section>

    <section class="panel route-panel">
      <div class="panel-head">
        <span class="title">应急避险路径规划</span>
        <button class="btn" :class="{ active: store.pickingOrigin }" @click="store.startPickOrigin()">
          {{ store.pickingOrigin ? '请点选地图…' : '点选受灾点' }}
        </button>
      </div>
      <div class="panel-body">
        <template v-if="store.route">
          <div class="shelter">
            <div class="shelter-name">{{ store.route.target_shelter.name }}</div>
            <div class="muted">{{ store.route.target_shelter.address || '—' }} · 容量 {{ store.route.target_shelter.capacity }} 人</div>
          </div>
          <div class="route-stats">
            <div><span class="k">路程</span><span class="v mono">{{ store.route.target_shelter.distance_m }}<small>m</small></span></div>
            <div><span class="k">步行</span><span class="v mono">{{ store.route.estimated_time_min }}<small>min</small></span></div>
            <div><span class="k">车行</span><span class="v mono">{{ store.route.estimated_drive_min }}<small>min</small></span></div>
            <div><span class="k">避开阻断</span><span class="v mono danger">{{ store.route.avoid_blocked_roads }}<small>条</small></span></div>
          </div>
          <div v-if="store.route.bypassed_danger_points?.length" class="bypass">
            <span class="muted">途经风险点：</span>
            <span v-for="p in store.route.bypassed_danger_points" :key="p" class="chip danger">{{ p }}</span>
          </div>
        </template>
        <div v-else class="empty">
          点选任意受淹点位，系统将检索最近且容量充足的避难场所，并避开积水阻断路段计算安全转移路线。
        </div>
      </div>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()
const stationTotal = computed(() => store.stations.filter((s) => s.properties.station_type === 'waterlogging').length)
const maxBd = computed(() => Math.max(1, ...(store.impact?.district_breakdown || []).map((d: any) => d.buildings)))
const engineClass = computed(() => (store.impact?.engine === 'postgis' ? 'info' : 'ok'))

function levelClass(level?: number) {
  if ((level ?? 0) >= 3) return 'danger'
  if ((level ?? 0) === 2) return 'orange'
  if ((level ?? 0) === 1) return 'warn'
  return 'ok'
}
function barWidth(depth: number) {
  return `${Math.min(100, (depth / 90) * 100)}%`
}
function bdWidth(n: number) {
  return `${(n / maxBd.value) * 100}%`
}
function focusStation(s: any) {
  store.selected = s
  store.pushCommand('FLY_TO', { center: s.geometry.coordinates, zoom: 16, pitch: 58 })
}
</script>

<style scoped>
.right {
  position: absolute;
  right: 16px;
  top: 118px;
  bottom: 152px;
  width: 352px;
  z-index: 12;
  display: flex;
  flex-direction: column;
  gap: 10px;
  pointer-events: none;
}
.right > * {
  pointer-events: auto;
}
.station-panel {
  flex: 1.15;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.impact-panel {
  flex: 1.1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.route-panel {
  flex: 0.75;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
}
.row {
  display: flex;
  gap: 8px;
  padding: 5px 4px;
  border-bottom: 1px solid rgba(0, 224, 255, 0.07);
  cursor: pointer;
  transition: background 0.15s;
}
.row:hover {
  background: rgba(0, 224, 255, 0.07);
}
.rank {
  font-size: 11px;
  color: #4f7386;
  width: 18px;
  padding-top: 2px;
}
.row-main {
  flex: 1;
  min-width: 0;
}
.row-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}
.name {
  font-size: 12.5px;
  color: #d5f2ff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.depth {
  font-size: 13px;
  white-space: nowrap;
}
.depth small {
  font-size: 9.5px;
  color: #6b93a5;
  margin-left: 1px;
}
.bar {
  height: 3px;
  background: rgba(0, 224, 255, 0.1);
  border-radius: 2px;
  margin: 4px 0 3px;
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  transition: width 0.5s ease;
}
.bar i.ok {
  background: linear-gradient(90deg, #157f60, #2ee6a8);
}
.bar i.warn {
  background: linear-gradient(90deg, #8a6a1f, #ffd666);
}
.bar i.orange {
  background: linear-gradient(90deg, #8a4a12, #ffa940);
}
.bar i.danger {
  background: linear-gradient(90deg, #8a1c1c, #ff5252);
}
.row-bottom {
  display: flex;
  justify-content: space-between;
  font-size: 10.5px;
}
.danger {
  color: #ff6b6b;
}
.orange {
  color: #ffa940;
}
.warn {
  color: #ffd666;
}
.ok {
  color: #2ee6a8;
}
.empty {
  font-size: 12px;
  color: #6b93a5;
  line-height: 1.75;
  padding: 6px 2px;
}
.impact-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.mini {
  display: flex;
  flex-direction: column;
  padding: 6px 7px;
  border: 1px solid rgba(0, 224, 255, 0.12);
  border-radius: 3px;
  background: rgba(0, 80, 110, 0.12);
}
.mini .k {
  font-size: 10.5px;
  color: #6b93a5;
}
.mini .v {
  font-size: 16px;
  color: #eafcff;
}
.mini .v small {
  font-size: 9.5px;
  color: #6b93a5;
  margin-left: 2px;
}
.impact-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
  font-size: 10.5px;
}
.breakdown {
  margin-top: 8px;
}
.bd-title {
  font-size: 11px;
  color: #7fa9bb;
  margin-bottom: 4px;
}
.bd-row {
  display: grid;
  grid-template-columns: 52px 1fr 34px;
  gap: 6px;
  align-items: center;
  font-size: 11px;
  line-height: 1.9;
  color: #a8cfdd;
}
.bd-bar {
  height: 4px;
  background: rgba(0, 224, 255, 0.1);
  border-radius: 2px;
  overflow: hidden;
}
.bd-bar i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #0d7ea8, #00e5ff);
}
.shelter-name {
  font-size: 13.5px;
  color: #b6ffe4;
  margin-bottom: 2px;
}
.route-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  margin-top: 8px;
}
.route-stats > div {
  display: flex;
  flex-direction: column;
}
.route-stats .k {
  font-size: 10px;
  color: #6b93a5;
}
.route-stats .v {
  font-size: 15px;
  color: #d5f2ff;
}
.route-stats .v small {
  font-size: 9px;
  color: #6b93a5;
  margin-left: 1px;
}
.bypass {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
  font-size: 10.5px;
}
</style>
