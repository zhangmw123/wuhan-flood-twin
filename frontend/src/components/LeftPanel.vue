<template>
  <aside class="left">
    <div class="kpis">
      <div class="kpi">
        <span class="label">超警监测点</span>
        <span class="value mono" :class="{ warn: alertCount > 0 }">{{ alertCount }}<small>处</small></span>
      </div>
      <div class="kpi" :class="{ danger: dangerCount > 0 }">
        <span class="label">红色封路点</span>
        <span class="value mono">{{ dangerCount }}<small>处</small></span>
      </div>
      <div class="kpi">
        <span class="label">最大积水深度</span>
        <span class="value mono" :class="{ danger: maxDepth >= 50, warn: maxDepth >= 30 && maxDepth < 50 }">
          {{ maxDepth.toFixed(0) }}<small>cm</small>
        </span>
      </div>
      <div class="kpi">
        <span class="label">监测站点总数</span>
        <span class="value mono">{{ stationTotal }}<small>个</small></span>
      </div>
    </div>

    <div class="left-scroll">
      <section class="panel chart-panel">
        <div class="panel-head">
          <span class="title">汉口水文站 水位-流量过程线</span>
          <span class="chip info">{{ store.mode === 'simulation' ? '推演' : '实时' }}</span>
        </div>
        <div class="panel-body chart-body">
          <EChart :option="levelOption" />
        </div>
      </section>

      <section class="panel chart-panel">
        <div class="panel-head">
          <span class="title">各区小时雨强(mm/h)</span>
          <span class="chip info">TOP 10</span>
        </div>
        <div class="panel-body chart-body">
          <EChart :option="rainOption" />
        </div>
      </section>

      <section class="panel chart-panel">
        <div class="panel-head">
          <span class="title">各区最大积水深度(cm)</span>
          <span class="chip" :class="dangerCount ? 'danger' : 'ok'">
            {{ dangerCount ? '存在封路' : '总体可控' }}
          </span>
        </div>
        <div class="panel-body chart-body">
          <EChart :option="depthOption" />
        </div>
      </section>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EChart from './EChart.vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()

const axisStyle = {
  axisLine: { lineStyle: { color: 'rgba(0,224,255,0.28)' } },
  axisLabel: { color: '#7fa9bb', fontSize: 9.5 },
  splitLine: { lineStyle: { color: 'rgba(0,224,255,0.07)' } },
  axisTick: { show: false },
}

const alertCount = computed(() => store.stationMeta?.alert_station_count ?? 0)
const dangerCount = computed(() => store.stationMeta?.danger_station_count ?? 0)
const stationTotal = computed(() => store.stations.filter((s) => s.properties.station_type === 'waterlogging').length)
const maxDepth = computed(() => {
  const arr = store.stations
    .filter((s) => s.properties.station_type === 'waterlogging')
    .map((s) => s.properties.current_depth_cm || 0)
  return arr.length ? Math.max(...arr) : 0
})

const levelOption = computed(() => {
  const series = store.waterLevel?.series || []
  const hours = series.map((d: any) => d.hour)
  const level = series.map((d: any) => d.level_m)
  const flow = series.map((d: any) => Math.round(d.flow_m3s / 1000))
  const warn = store.meta?.warning_level_m ?? 27.3
  const guarantee = store.meta?.guarantee_level_m ?? 29.73
  return {
    grid: { left: 40, right: 38, top: 26, bottom: 22 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(4,20,30,0.94)',
      borderColor: 'rgba(0,229,255,0.4)',
      textStyle: { color: '#d8f4ff', fontSize: 11 },
    },
    legend: {
      data: ['水位(m)', '流量(千m³/s)'],
      right: 4,
      top: 0,
      itemWidth: 10,
      itemHeight: 6,
      textStyle: { color: '#7fa9bb', fontSize: 10 },
    },
    xAxis: { type: 'category', data: hours, boundaryGap: false, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, interval: 3 } },
    yAxis: [
      { type: 'value', name: 'm', min: 26, max: 30, nameTextStyle: { color: '#5d8394', fontSize: 9 }, ...axisStyle },
      { type: 'value', name: '千m³/s', nameTextStyle: { color: '#5d8394', fontSize: 9 }, ...axisStyle, splitLine: { show: false } },
    ],
    series: [
      {
        name: '水位(m)',
        type: 'line',
        data: level,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, color: '#00e5ff', shadowColor: 'rgba(0,229,255,0.7)', shadowBlur: 10 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(0,229,255,0.35)' },
              { offset: 1, color: 'rgba(0,229,255,0.01)' },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          label: { color: '#ffb545', fontSize: 9, formatter: '警戒 {c}m' },
          lineStyle: { color: '#ffb545', type: 'dashed', width: 1 },
          data: [{ yAxis: warn }, { yAxis: guarantee, lineStyle: { color: '#ff5252' }, label: { formatter: '保证 {c}m', color: '#ff5252' } }],
        },
      },
      {
        name: '流量(千m³/s)',
        type: 'line',
        yAxisIndex: 1,
        data: flow,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.4, color: '#9b8cff', type: 'dotted' },
      },
    ],
  }
})

const rainOption = computed(() => {
  const data = [...(store.stats || [])].sort((a, b) => b.rain_1h_mm - a.rain_1h_mm).slice(0, 10).reverse()
  return {
    grid: { left: 56, right: 26, top: 8, bottom: 18 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(4,20,30,0.94)',
      borderColor: 'rgba(0,229,255,0.4)',
      textStyle: { color: '#d8f4ff', fontSize: 11 },
    },
    xAxis: { type: 'value', ...axisStyle },
    yAxis: {
      type: 'category',
      data: data.map((d) => d.district),
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        data: data.map((d) => d.rain_1h_mm),
        barWidth: 9,
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [
              { offset: 0, color: 'rgba(0,120,180,0.55)' },
              { offset: 1, color: '#00e5ff' },
            ],
          },
        },
        label: { show: true, position: 'right', color: '#9fe9ff', fontSize: 9.5, formatter: '{c}' },
      },
    ],
  }
})

const depthOption = computed(() => {
  const data = [...(store.stats || [])].sort((a, b) => b.max_depth_cm - a.max_depth_cm).slice(0, 8)
  return {
    grid: { left: 40, right: 14, top: 12, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(4,20,30,0.94)',
      borderColor: 'rgba(0,229,255,0.4)',
      textStyle: { color: '#d8f4ff', fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.district.replace('区', '')),
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, interval: 0, fontSize: 9.5, rotate: 30 },
    },
    yAxis: { type: 'value', max: (v: any) => Math.max(60, Math.ceil(v.max / 10) * 10), ...axisStyle },
    series: [
      {
        type: 'bar',
        data: data.map((d) => ({
          value: d.max_depth_cm,
          itemStyle: {
            color:
              d.max_depth_cm >= 50 ? '#ff5252' : d.max_depth_cm >= 30 ? '#ffa940' : d.max_depth_cm >= 15 ? '#ffd666' : '#2ee6a8',
          },
        })),
        barWidth: 12,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
        markLine: {
          silent: true,
          symbol: 'none',
          label: { color: '#ff5252', fontSize: 9, formatter: '封路 50cm' },
          lineStyle: { color: 'rgba(255,82,82,0.65)', type: 'dashed' },
          data: [{ yAxis: 50 }],
        },
      },
    ],
  }
})
</script>

<style scoped>
.left {
  position: absolute;
  left: 16px;
  top: 118px;
  bottom: 152px;
  width: 336px;
  z-index: 12;
  display: flex;
  flex-direction: column;
  gap: 10px;
  pointer-events: none;
}
.left > * {
  pointer-events: auto;
}
.kpis {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  flex: none;
}

/* 图表区独立滚动：每张图固定高度，不再被均分压扁 */
.left-scroll {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 224, 255, 0.28) transparent;
}

.chart-panel {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
}
/* 过程线需容纳双 Y 轴 + 图例 + 警戒/保证水印线 */
.left-scroll .chart-panel:nth-child(1) {
  height: 246px;
}
/* 10 个区的横向条形图 */
.left-scroll .chart-panel:nth-child(2) {
  height: 316px;
}
/* 8 个区的柱状图 + 旋转标签 */
.left-scroll .chart-panel:nth-child(3) {
  height: 262px;
}

.chart-body {
  flex: 1;
  min-height: 0;
  padding: 4px 6px 6px;
}
</style>
