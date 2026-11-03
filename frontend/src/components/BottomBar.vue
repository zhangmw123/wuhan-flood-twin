<template>
  <footer class="bottom panel">
    <!-- 时间轴 -->
    <div class="row timeline-row">
      <div class="play">
        <button class="play-btn" :class="{ playing: store.playing }" @click="store.togglePlay()">
          {{ store.playing ? '❚❚' : '▶' }}
        </button>
        <div class="clock">
          <span class="mono">{{ hourText }}</span>
          <small>{{ store.mode === 'simulation' ? '沙盘时刻' : '实时' }}</small>
        </div>
      </div>

      <div class="slider-wrap">
        <input
          class="timeline"
          type="range"
          min="0"
          max="24"
          step="0.1"
          :value="localHour"
          :disabled="store.mode !== 'simulation'"
          @input="onDrag"
          @change="commit"
        />
        <div class="ticks">
          <span v-for="h in ticks" :key="h" :class="{ on: localHour >= h }">{{ String(h).padStart(2, '0') }}</span>
        </div>
      </div>

      <div class="rain-mini">
        <span class="muted">当前雨强</span>
        <b class="mono">{{ (store.stationMeta?.city_avg_rain_1h_mm ?? 0).toFixed(1) }} mm/h</b>
      </div>
    </div>

    <!-- 推演控制 -->
    <div class="row control-row">
      <div class="group">
        <span class="g-label">累计雨量</span>
        <input class="rain-slider" type="range" min="50" max="250" step="10" v-model.number="store.rainfall" />
        <span class="g-value mono">{{ store.rainfall }}<small>mm</small></span>
      </div>

      <div class="group">
        <span class="g-label">历时</span>
        <button
          v-for="h in [1, 3, 6]"
          :key="h"
          class="btn tiny"
          :class="{ active: store.duration === h }"
          @click="store.duration = h"
        >
          {{ h }}h
        </button>
      </div>

      <button class="btn primary" :disabled="store.impactLoading" @click="store.runImpact()">
        {{ store.impactLoading ? '推演计算中…' : '开始内涝推演' }}
      </button>
      <button class="btn ghost tiny" v-if="store.impact" @click="store.clearImpact()">清除淹没</button>

      <button
        class="btn report-btn"
        :disabled="store.briefingLoading"
        title="按当前雨量/历时情景生成防汛会商简报：可预览、下载 Markdown、打印为 PDF"
        @click="store.openBriefing()"
      >
        {{ store.briefingLoading ? '生成中…' : '会商简报' }}
      </button>

      <div class="divider"></div>

      <div class="group layers">
        <span class="g-label">图层</span>
        <button
          v-for="l in layerList"
          :key="l.key"
          class="btn tiny"
          :class="{ active: (store.layers as any)[l.key] }"
          @click="(store.layers as any)[l.key] = !(store.layers as any)[l.key]"
        >
          {{ l.label }}
        </button>
      </div>

      <button class="btn copilot-btn" @click="store.copilotOpen = !store.copilotOpen">
        <span class="spark">✦</span> AI 防汛调度 Copilot
      </button>
    </div>
  </footer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()
const localHour = ref(store.hour)
const dragging = ref(false)

const ticks = [0, 3, 6, 9, 12, 15, 18, 21, 24]
const hourText = computed(() => {
  const h = Math.floor(localHour.value)
  const m = Math.round((localHour.value - h) * 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
})

watch(
  () => store.hour,
  (h) => {
    if (!dragging.value) localHour.value = h
  },
)

const layerList = [
  { key: 'buildings', label: '三维建筑' },
  { key: 'roads', label: '路网' },
  { key: 'water', label: '水系' },
  { key: 'districts', label: '行政区' },
  { key: 'stations', label: '监测点' },
  { key: 'flood', label: '淹没区' },
  { key: 'shelters', label: '避难所' },
]

function onDrag(e: Event) {
  dragging.value = true
  localHour.value = Number((e.target as HTMLInputElement).value)
}

async function commit() {
  dragging.value = false
  await store.seek(localHour.value)
}
</script>

<style scoped>
.bottom {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 12px;
  height: 128px;
  z-index: 15;
  padding: 10px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.timeline-row {
  flex: 1;
}

.play {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  width: 132px;
}
.play-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  background: rgba(0, 224, 255, 0.1);
  color: #cdf6ff;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
}
.play-btn.playing {
  border-color: rgba(36, 224, 160, 0.7);
  color: #9dffdc;
  box-shadow: 0 0 14px rgba(36, 224, 160, 0.35);
}
.play-btn:hover {
  background: rgba(0, 224, 255, 0.22);
}
.clock {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}
.clock span {
  font-size: 16px;
  color: #eafcff;
  letter-spacing: 0.5px;
}
.clock small {
  font-size: 10px;
  color: #5d8394;
}

.slider-wrap {
  flex: 1;
  min-width: 0;
}
.timeline {
  width: 100%;
}
.timeline:disabled {
  opacity: 0.4;
}
.ticks {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 9.5px;
  color: #3f6172;
  font-family: var(--mono);
}
.ticks .on {
  color: #7fe6ff;
}

.rain-mini {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex: none;
  width: 108px;
  line-height: 1.35;
}
.rain-mini span {
  font-size: 10px;
}
.rain-mini b {
  font-size: 13px;
  color: #7fe6ff;
}

.control-row {
  gap: 12px;
}

.group {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
}
.g-label {
  font-size: 11px;
  color: #6b93a5;
  white-space: nowrap;
}
.g-value {
  font-size: 12px;
  color: #cdf3ff;
  width: 56px;
}
.g-value small {
  font-size: 9px;
  color: #6b93a5;
  margin-left: 1px;
}
.rain-slider {
  width: 168px;
}
.btn.tiny {
  padding: 3px 9px;
  font-size: 11px;
}
.btn.primary {
  border-color: rgba(0, 229, 255, 0.8);
  background: linear-gradient(90deg, rgba(0, 160, 200, 0.35), rgba(0, 229, 255, 0.25));
  box-shadow: 0 0 16px rgba(0, 229, 255, 0.28);
}
.divider {
  width: 1px;
  height: 20px;
  background: rgba(0, 224, 255, 0.16);
}
.layers {
  flex-wrap: nowrap;
  gap: 5px;
}
.copilot-btn {
  margin-left: auto;
  border-color: rgba(177, 140, 255, 0.6);
  background: linear-gradient(90deg, rgba(90, 60, 180, 0.32), rgba(140, 90, 255, 0.22));
  color: #e6dcff;
}
.report-btn {
  border-color: rgba(255, 196, 92, 0.55);
  background: linear-gradient(90deg, rgba(180, 120, 20, 0.3), rgba(255, 196, 92, 0.18));
  color: #ffe4b0;
  padding: 4px 11px;
  font-size: 11.5px;
}
.report-btn:hover:not(:disabled) {
  box-shadow: 0 0 15px rgba(255, 196, 92, 0.4);
}
.report-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}

.copilot-btn:hover {
  box-shadow: 0 0 16px rgba(150, 100, 255, 0.45);
}
.spark {
  color: #c9b3ff;
}
</style>
