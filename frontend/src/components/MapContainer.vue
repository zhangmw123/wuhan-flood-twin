<template>
  <div class="map-wrap">
    <div ref="mapEl" class="map-canvas"></div>

    <!-- 图例 -->
    <div class="legend panel">
      <div class="legend-title">图例</div>
      <div class="legend-row"><i class="dot ok"></i>正常监测点 (&lt;30cm)</div>
      <div class="legend-row"><i class="dot warn"></i>黄色预警 (30~40cm)</div>
      <div class="legend-row"><i class="dot orange"></i>橙色预警 (40~50cm)</div>
      <div class="legend-row"><i class="dot danger"></i>红色封路 (≥50cm)</div>
      <div class="legend-row"><i class="swatch flood"></i>内涝淹没区</div>
      <div class="legend-row"><i class="swatch blocked"></i>积水阻断路段</div>
      <div class="legend-row"><i class="swatch route"></i>避险转移路线</div>
    </div>

    <!-- 视角提示 -->
    <div v-if="store.pickingOrigin" class="pick-tip fade-up">
      点击地图任意位置，规划至最近避难所的安全路线（Esc 取消）
    </div>

    <!-- 底图加载失败提示 -->
    <div v-if="styleFailed" class="style-fallback">
      在线底图不可达，已切换离线底图（业务图层不受影响）
    </div>

    <!-- 数据来源与许可（地图类应用合规要求） -->
    <div class="data-src mono">
      底图 © CARTO / OpenStreetMap · 水系 © OpenStreetMap (ODbL) · 建筑与路网 OSM · 积水深度与水位为模型推演
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { api } from '../api/floodApi'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()
const mapEl = ref<HTMLDivElement | null>(null)
const styleFailed = ref(false)

let map: maplibregl.Map
let raf = 0
let loadTimer: number | null = null
let popup: maplibregl.Popup | null = null
let routePulse = 0
let routeProfile: { pts: [number, number][]; cum: number[]; total: number } | null = null

const ONLINE_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const FALLBACK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#050c12' } }],
} as any

const empty = () => ({ type: 'FeatureCollection', features: [] })

/* ------------------------------------------------------------------ 初始化 */

onMounted(async () => {
  map = new maplibregl.Map({
    container: mapEl.value!,
    style: ONLINE_STYLE,
    center: [114.3055, 30.5928],
    zoom: 11.8,
    pitch: 47,
    bearing: -14,
    antialias: true,
    attributionControl: { compact: true },
  })

  map.on('error', (e: any) => {
    const msg = String(e?.error?.message || '')
    if (!styleFailed.value && /style|fetch|Load|Failed/i.test(msg) && !map.isStyleLoaded()) {
      styleFailed.value = true
      map.setStyle(FALLBACK_STYLE)
    }
  })

  ;(window as any).__floodMap = map
  map.on('load', async () => {
    addBaseLayers()
    addDynamicLayers()
    startPulse()
    await loadStaticLayers()
    await loadViewport()
  })

  map.on('moveend', scheduleViewportLoad)
  map.on('click', onMapClick)
  map.on('mousemove', (e) => {
    if (!map.getLayer('stations-circle')) return
    const hit = map.queryRenderedFeatures(e.point, { layers: ['stations-circle'] })
    map.getCanvas().style.cursor = hit.length ? 'pointer' : store.pickingOrigin ? 'crosshair' : ''
  })

  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.removeEventListener('keydown', onKey)
  if (loadTimer) window.clearTimeout(loadTimer)
  map?.remove()
})

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.pickingOrigin) store.pickingOrigin = false
}

/* ------------------------------------------------------------------ 图层骨架 */

function addBaseLayers() {
  map.addSource('districts', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'districts-fill',
    type: 'fill',
    source: 'districts',
    paint: { 'fill-color': '#0a2a3a', 'fill-opacity': 0.35 },
  })
  map.addLayer({
    id: 'districts-line',
    type: 'line',
    source: 'districts',
    paint: { 'line-color': '#28c8e8', 'line-width': 1.1, 'line-opacity': 0.65 },
  })
  map.addLayer({
    id: 'districts-label',
    type: 'symbol',
    source: 'districts',
    minzoom: 10,
    layout: {
      'text-field': ['get', 'name'],
      'text-font': ['Open Sans Regular'],
      'text-size': 12,
      'text-letter-spacing': 0.1,
    },
    paint: { 'text-color': '#7fe6ff', 'text-halo-color': '#02121b', 'text-halo-width': 1.6 },
  })

  map.addSource('water', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'water-fill',
    type: 'fill',
    source: 'water',
    paint: { 'fill-color': '#0b3f5c', 'fill-opacity': 0.78 },
  })
  map.addLayer({
    id: 'water-line',
    type: 'line',
    source: 'water',
    paint: { 'line-color': '#2ea8d8', 'line-width': 1.2, 'line-opacity': 0.9 },
  })

  map.addSource('shelters', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'shelters-circle',
    type: 'circle',
    source: 'shelters',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 16, 6],
      'circle-color': '#24e0a0',
      'circle-stroke-color': '#04120d',
      'circle-stroke-width': 1,
      'circle-opacity': 0.9,
    },
  })
  map.addLayer({
    id: 'shelters-label',
    type: 'symbol',
    source: 'shelters',
    minzoom: 13.5,
    layout: {
      'text-field': ['get', 'name'],
      'text-font': ['Open Sans Regular'],
      'text-size': 10,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
    },
    paint: { 'text-color': '#9df3d0', 'text-halo-color': '#02120d', 'text-halo-width': 1.2 },
  })
}

function addDynamicLayers() {
  map.addSource('buildings', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'buildings-3d',
    type: 'fill-extrusion',
    source: 'buildings',
    minzoom: 11.5,
    paint: {
      'fill-extrusion-height': ['*', ['get', 'elevation'], 1],
      'fill-extrusion-base': 0,
      'fill-extrusion-color': [
        'interpolate', ['linear'], ['get', 'elevation'],
        0, '#ff4d4f',
        12, '#7a3b52',
        28, '#1890ff',
        60, '#00c6ff',
        100, '#00f2fe',
      ],
      'fill-extrusion-opacity': 0.82,
    },
  })

  map.addSource('roads', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'roads-line',
    type: 'line',
    source: 'roads',
    minzoom: 10.5,
    paint: {
      'line-color': '#4a7f92',
      'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.4, 16, 1.6],
      'line-opacity': 0.55,
    },
  })

  map.addSource('blocked-roads', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'blocked-roads-line',
    type: 'line',
    source: 'blocked-roads',
    paint: {
      'line-color': '#ff4d4f',
      'line-width': ['interpolate', ['linear'], ['zoom'], 11, 2, 16, 6],
      'line-opacity': 0.95,
      'line-blur': 0.4,
    },
  })

  map.addSource('flood-zones', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'flood-fill',
    type: 'fill',
    source: 'flood-zones',
    paint: {
      'fill-color': [
        'interpolate', ['linear'], ['get', 'depth_cm'],
        15, '#1e6fff',
        30, '#00b0ff',
        50, '#00e5ff',
        80, '#ffd166',
      ],
      'fill-opacity': 0.42,
    },
  })
  map.addLayer({
    id: 'flood-line',
    type: 'line',
    source: 'flood-zones',
    paint: { 'line-color': '#7fe9ff', 'line-width': 1.1, 'line-opacity': 0.85, 'line-dasharray': [2, 1.6] },
  })

  map.addSource('stations', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'stations-halo',
    type: 'circle',
    source: 'stations',
    filter: ['>=', ['get', 'alert_level'], 2],
    paint: {
      'circle-radius': 14,
      'circle-color': ['match', ['get', 'alert_level'], 3, '#ff5252', 2, '#ffa940', '#00e5ff'],
      'circle-opacity': 0.22,
      'circle-blur': 0.4,
    },
  })
  map.addLayer({
    id: 'stations-circle',
    type: 'circle',
    source: 'stations',
    filter: ['==', ['get', 'station_type'], 'waterlogging'],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['get', 'current_depth_cm'], 0, 4.5, 50, 9, 90, 13],
      'circle-color': [
        'match', ['get', 'alert_level'],
        3, '#ff3b3b',
        2, '#ffa940',
        1, '#ffd666',
        0, '#2ee6a8',
      ],
      'circle-stroke-color': '#031318',
      'circle-stroke-width': 1.2,
      'circle-opacity': 0.95,
    },
  })
  map.addLayer({
    id: 'stations-hydro',
    type: 'circle',
    source: 'stations',
    filter: ['==', ['get', 'station_type'], 'hydrology'],
    paint: {
      'circle-radius': 6,
      'circle-color': '#00e5ff',
      'circle-stroke-color': '#031318',
      'circle-stroke-width': 1.4,
    },
  })
  map.addLayer({
    id: 'stations-pump',
    type: 'circle',
    source: 'stations',
    filter: ['==', ['get', 'station_type'], 'pump'],
    paint: {
      'circle-radius': 4,
      'circle-color': '#9b8cff',
      'circle-opacity': 0.85,
      'circle-stroke-color': '#0a0616',
      'circle-stroke-width': 1,
    },
  })

  map.addSource('buffer', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'buffer-fill',
    type: 'fill',
    source: 'buffer',
    paint: { 'fill-color': '#00e5ff', 'fill-opacity': 0.12 },
  })
  map.addLayer({
    id: 'buffer-line',
    type: 'line',
    source: 'buffer',
    paint: { 'line-color': '#7fe9ff', 'line-width': 1.6, 'line-dasharray': [3, 2] },
  })

  map.addSource('route', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'route-line',
    type: 'line',
    source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#24e0a0',
      'line-width': 5,
      'line-opacity': 0.9,
      'line-blur': 0.3,
    },
  })
  map.addLayer({
    id: 'route-glow',
    type: 'line',
    source: 'route',
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': '#a8ffe0', 'line-width': 12, 'line-opacity': 0.18, 'line-blur': 6 },
  })

  map.addSource('route-cursor', { type: 'geojson', data: empty() })
  map.addLayer({
    id: 'route-cursor',
    type: 'circle',
    source: 'route-cursor',
    paint: {
      'circle-radius': 6,
      'circle-color': '#eafff7',
      'circle-stroke-color': '#24e0a0',
      'circle-stroke-width': 2,
    },
  })
}

/* ------------------------------------------------------------------ 数据装载 */

async function loadStaticLayers() {
  try {
    const [districts, water, shelters] = await Promise.all([
      api.layerStatic('districts'),
      api.layerStatic('water'),
      api.layerStatic('shelters'),
    ])
    ;(map.getSource('districts') as maplibregl.GeoJSONSource)?.setData(districts)
    ;(map.getSource('water') as maplibregl.GeoJSONSource)?.setData(water)
    ;(map.getSource('shelters') as maplibregl.GeoJSONSource)?.setData(shelters)
  } catch (e) {
    console.warn('静态图层加载失败', e)
  }
}

function scheduleViewportLoad() {
  if (loadTimer) window.clearTimeout(loadTimer)
  loadTimer = window.setTimeout(loadViewport, 320)
}

async function loadViewport() {
  const z = map.getZoom()
  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
  try {
    if (z >= 11.2 && store.layers.buildings) {
      const fc = await api.layer('buildings', bbox, z > 14 ? 26000 : 16000)
      ;(map.getSource('buildings') as maplibregl.GeoJSONSource)?.setData(fc)
    } else {
      ;(map.getSource('buildings') as maplibregl.GeoJSONSource)?.setData(empty())
    }
    if (z >= 10.5 && store.layers.roads) {
      const fc = await api.layer('roads', bbox, 14000)
      ;(map.getSource('roads') as maplibregl.GeoJSONSource)?.setData(fc)
    } else {
      ;(map.getSource('roads') as maplibregl.GeoJSONSource)?.setData(empty())
    }
  } catch (e) {
    console.warn('视域图层加载失败', e)
  }
}

/* ------------------------------------------------------------------ 站点与脉冲 */

watch(
  () => store.stations,
  (fc) => {
    ;(map.getSource('stations') as maplibregl.GeoJSONSource)?.setData(fc as any)
  },
  { deep: false },
)

function startPulse() {
  const loop = () => {
    const t = performance.now() / 1000
    if (map && map.getLayer('stations-halo')) {
      map.setPaintProperty('stations-halo', 'circle-radius', 10 + Math.sin(t * 2.2) * 7 + 8)
      map.setPaintProperty('stations-halo', 'circle-opacity', 0.16 + Math.abs(Math.sin(t * 2.2)) * 0.22)
    }
    if (routeProfile && map.getLayer('route-cursor')) {
      routePulse = (routePulse + 0.9) % 1
      const p = pointAt(routeProfile, routePulse * routeProfile.total)
      ;(map.getSource('route-cursor') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: {} }],
      })
    }
    raf = requestAnimationFrame(loop)
  }
  raf = requestAnimationFrame(loop)
}

function pointAt(profile: { pts: [number, number][]; cum: number[] }, dist: number): [number, number] {
  const { pts, cum } = profile
  for (let i = 1; i < pts.length; i++) {
    if (cum[i] >= dist) {
      const seg = cum[i] - cum[i - 1] || 1
      const r = (dist - cum[i - 1]) / seg
      return [
        pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * r,
        pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * r,
      ]
    }
  }
  return pts[pts.length - 1]
}

/* ------------------------------------------------------------------ 交互 */

function onMapClick(e: maplibregl.MapMouseEvent) {
  if (store.pickingOrigin) {
    store.planRoute(e.lngLat.lng, e.lngLat.lat)
    return
  }
  const feats = map.queryRenderedFeatures(e.point, { layers: ['stations-circle', 'stations-hydro'] })
  if (!feats.length) {
    popup?.remove()
    return
  }
  const p: any = feats[0].properties
  const depth = p.current_depth_cm ?? 0
  const lvl = p.alert_level ?? 0
  const tag = lvl >= 3 ? '红色封路' : lvl === 2 ? '橙色预警' : lvl === 1 ? '黄色预警' : '正常'
  const color = lvl >= 3 ? '#ff5252' : lvl === 2 ? '#ffa940' : lvl === 1 ? '#ffd666' : '#2ee6a8'
  const html = `
    <div class="popup-title">${p.name}</div>
    <div class="popup-row"><span>所属行政区</span><b>${p.district || '-'}</b></div>
    <div class="popup-row"><span>当前积水</span><b style="color:${color}">${depth} cm</b></div>
    <div class="popup-row"><span>小时雨强</span><b>${p.rain_1h_mm ?? '-'} mm/h</b></div>
    <div class="popup-row"><span>预警状态</span><b style="color:${color}">${tag}</b></div>
    <div class="popup-row"><span>联动泵站</span><b>${p.pump_name || '-'}</b></div>
  `
  popup?.remove()
  popup = new maplibregl.Popup({ closeButton: true, offset: 12, maxWidth: '260px' })
    .setLngLat(e.lngLat)
    .setHTML(html)
    .addTo(map)
  const st = store.stations.find((s) => s.properties.station_code === p.station_code)
  if (st) store.selected = st
}

/* ------------------------------------------------------------------ 地图指令 */

watch(
  () => store.commands.length,
  () => {
    const cmds = [...store.commands]
    cmds.forEach((c) => {
      try {
        runCommand(c.action, c.payload)
      } finally {
        store.ackCommand(c.id)
      }
    })
  },
)

function runCommand(action: string, payload: any) {
  switch (action) {
    case 'FLY_TO': {
      const center = payload.center || [payload.lng, payload.lat]
      map.flyTo({
        center,
        zoom: payload.zoom ?? 16,
        pitch: payload.pitch ?? 55,
        duration: 1600,
        essential: true,
      })
      break
    }
    case 'SHOW_FLOOD': {
      const fc = payload.geojson || empty()
      ;(map.getSource('flood-zones') as maplibregl.GeoJSONSource)?.setData(fc)
      break
    }
    case 'SHOW_ROUTE': {
      const route = payload.route_geojson || payload.route
      if (!route) break
      const coords: [number, number][] = route.geometry.coordinates
      ;(map.getSource('route') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection',
        features: [route],
      })
      routeProfile = buildProfile(coords)
      const b = coords.reduce(
        (acc, c) => acc.extend(c),
        new maplibregl.LngLatBounds(coords[0], coords[0]),
      )
      map.fitBounds(b, { padding: { top: 160, bottom: 220, left: 380, right: 400 }, duration: 1400 })
      break
    }
    case 'DRAW_BUFFER': {
      const gj = payload.geojson
      ;(map.getSource('buffer') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection',
        features: gj ? [gj] : [],
      })
      break
    }
    case 'HIGHLIGHT_SHELTER': {
      store.layers.shelters = true
      break
    }
  }
}

function buildProfile(coords: [number, number][]) {
  const cum = [0]
  for (let i = 1; i < coords.length; i++) {
    cum.push(cum[i - 1] + haversine(coords[i - 1], coords[i]))
  }
  return { pts: coords, cum, total: cum[cum.length - 1] }
}

function haversine(a: [number, number], b: [number, number]) {
  const R = 6371008.8
  const dLat = ((b[1] - a[1]) * Math.PI) / 180
  const dLon = ((b[0] - a[0]) * Math.PI) / 180
  const la1 = (a[1] * Math.PI) / 180
  const la2 = (b[1] * Math.PI) / 180
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

/* ------------------------------------------------------------------ 图层显隐 */

watch(
  () => ({ ...store.layers }),
  (l) => {
    if (!map || !map.isStyleLoaded()) return
    const toggle = (id: string, on: boolean) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none')
    }
    toggle('water-fill', l.water)
    toggle('water-line', l.water)
    toggle('districts-fill', l.districts)
    toggle('districts-line', l.districts)
    toggle('districts-label', l.districts)
    toggle('buildings-3d', l.buildings)
    toggle('roads-line', l.roads)
    toggle('stations-halo', l.stations)
    toggle('stations-circle', l.stations)
    toggle('stations-hydro', l.stations)
    toggle('stations-pump', l.stations)
    toggle('flood-fill', l.flood)
    toggle('flood-line', l.flood)
    toggle('shelters-circle', l.shelters)
    toggle('shelters-label', l.shelters)
    if (!l.buildings) {
      ;(map.getSource('buildings') as maplibregl.GeoJSONSource)?.setData(empty())
    } else {
      scheduleViewportLoad()
    }
    if (!l.roads) (map.getSource('roads') as maplibregl.GeoJSONSource)?.setData(empty())
  },
  { deep: true },
)

/* ------------------------------------------------------------------ 灾情结果联动 */

watch(
  () => store.impact,
  (imp) => {
    const zones = imp?.inundation_polygons_geojson || empty()
    const blocked = imp?.blocked_roads_geojson || empty()
    ;(map.getSource('flood-zones') as maplibregl.GeoJSONSource)?.setData(zones)
    ;(map.getSource('blocked-roads') as maplibregl.GeoJSONSource)?.setData(blocked)
    if (imp) store.layers.flood = true
    // 全市推演时淹没区分散在各区，不强制定位；按行政区推演时飞至该区
    if (imp?.query?.district && map.getLayer('flood-fill')) {
      const b = new maplibregl.LngLatBounds()
      zones.features?.forEach((f: any) => f.geometry?.coordinates?.[0]?.forEach((c: any) => b.extend(c)))
      if (!b.isEmpty()) map.fitBounds(b, { padding: 120, duration: 1200, maxZoom: 14 })
    }
  },
)

defineExpose({ flyTo: (c: [number, number], z = 15) => map?.flyTo({ center: c, zoom: z, pitch: 55 }) })
</script>

<style scoped>
.map-wrap {
  position: absolute;
  inset: 0;
}
.map-canvas {
  position: absolute;
  inset: 0;
  background: #050c12;
}
.legend {
  position: absolute;
  left: 18px;
  bottom: 150px;
  padding: 9px 11px;
  font-size: 11.5px;
  color: var(--text-dim);
  z-index: 5;
  width: 168px;
}
.legend-title {
  color: #9fe9ff;
  margin-bottom: 6px;
  font-size: 12px;
  letter-spacing: 1px;
}
.legend-row {
  display: flex;
  align-items: center;
  gap: 7px;
  line-height: 1.85;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot.ok {
  background: #2ee6a8;
  box-shadow: 0 0 6px #2ee6a8;
}
.dot.warn {
  background: #ffd666;
  box-shadow: 0 0 6px #ffd666;
}
.dot.orange {
  background: #ffa940;
  box-shadow: 0 0 6px #ffa940;
}
.dot.danger {
  background: #ff5252;
  box-shadow: 0 0 6px #ff5252;
}
.swatch {
  width: 12px;
  height: 7px;
  display: inline-block;
  border-radius: 1px;
}
.swatch.flood {
  background: rgba(0, 176, 255, 0.55);
  border: 1px solid #7fe9ff;
}
.swatch.blocked {
  background: #ff4d4f;
}
.swatch.route {
  background: #24e0a0;
}
.pick-tip {
  position: absolute;
  left: 50%;
  top: 96px;
  transform: translateX(-50%);
  padding: 8px 18px;
  font-size: 13px;
  color: #052;
  background: rgba(0, 229, 255, 0.9);
  border-radius: 3px;
  z-index: 8;
  box-shadow: 0 0 24px rgba(0, 229, 255, 0.6);
}
.style-fallback {
  position: absolute;
  left: 50%;
  bottom: 128px;
  transform: translateX(-50%);
  font-size: 12px;
  color: var(--warn);
  background: rgba(30, 18, 0, 0.8);
  border: 1px solid rgba(255, 181, 69, 0.5);
  padding: 5px 12px;
  border-radius: 3px;
  z-index: 6;
}

/* 数据来源角标：放图例下方，不与底部控制条冲突 */
.data-src {
  position: absolute;
  left: 16px;
  bottom: 152px;
  z-index: 6;
  font-size: 9.5px;
  line-height: 1.6;
  letter-spacing: 0.2px;
  color: #547587;
  background: rgba(5, 14, 22, 0.55);
  border: 1px solid rgba(0, 224, 255, 0.08);
  border-radius: 3px;
  padding: 3px 9px;
  pointer-events: none;
  max-width: 46vw;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
