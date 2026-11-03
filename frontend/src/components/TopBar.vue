<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">
        <span class="wave"></span>
      </div>
      <div class="brand-text">
        <h1>江汉流域防汛抗旱与武汉城市内涝二三维孪生调度平台</h1>
        <p class="sub">Jianghan Basin Flood-Control &amp; Urban Waterlogging Digital Twin · 武汉市水务局</p>
      </div>
    </div>

    <div class="mode-switch">
      <button class="mode-btn" :class="{ active: store.mode === 'realtime' }" @click="store.setMode('realtime')">
        <i class="dot live" :class="{ on: store.mode === 'realtime' }"></i>实时气象接入
      </button>
      <button
        class="mode-btn"
        :class="{ active: store.mode === 'simulation' }"
        @click="store.setMode('simulation')"
      >
        <i class="dot sim" :class="{ on: store.mode === 'simulation' }"></i>2016·7·6 特大暴雨推演
      </button>
    </div>

    <!-- 真实感知数据接入状态：有实测值时明确标注，不把推演值当实测 -->
    <div
      v-if="store.stationMeta?.data_source === 'observed'"
      class="src-badge"
      :title="`数据来源：${store.stationMeta?.observed_provider || '外部接口'}`"
    >
      <i class="dot obs"></i>实测 {{ store.stationMeta?.observed_station_count }} 站
    </div>

    <div class="status">
      <div class="status-item">
        <span class="k">武汉关水位</span>
        <span class="v mono" :class="levelClass">{{ (store.hankou?.current_level_m ?? 0).toFixed(2) }}<small>m</small></span>
      </div>
      <div class="status-item">
        <span class="k">警戒 / 保证</span>
        <span class="v mono">{{ warning.toFixed(2) }} / {{ guarantee.toFixed(2) }}<small>m</small></span>
      </div>
      <div class="status-item">
        <span class="k">全市雨强</span>
        <span class="v mono">{{ (store.stationMeta?.city_avg_rain_1h_mm ?? 0).toFixed(1) }}<small>mm/h</small></span>
      </div>
      <div class="clock mono">{{ clock }}</div>
      <div class="sse" :class="store.sseStatus">
        <i></i>{{ store.sseStatus === 'online' ? 'SSE 在线' : store.sseStatus === 'offline' ? 'SSE 重连中' : 'SSE 连接中' }}
      </div>
    </div>
  </header>

  <div class="ticker" v-if="store.alerts.length">
    <span class="ticker-tag">实时预警</span>
    <div class="ticker-track">
      <div class="ticker-inner">
        <span v-for="(a, i) in doubled" :key="i" :class="['ticker-item', 'lv' + a.alert_level]">
          <b>{{ a.district }}</b> · {{ a.message }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()
const clock = ref('')
let timer = 0

const warning = computed(() => store.meta?.warning_level_m ?? 27.3)
const guarantee = computed(() => store.meta?.guarantee_level_m ?? 29.73)
const level = computed(() => store.hankou?.current_level_m ?? 0)
const levelClass = computed(() => (level.value >= guarantee.value ? 'danger' : level.value >= warning.value ? 'warn' : 'ok'))
const doubled = computed(() => [...store.alerts, ...store.alerts])

function tick() {
  const d = new Date()
  clock.value =
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
const pad = (n: number) => String(n).padStart(2, '0')

onMounted(() => {
  tick()
  timer = window.setInterval(tick, 1000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<style scoped>
.topbar {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 74px;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 20px;
  z-index: 20;
  background: linear-gradient(180deg, rgba(3, 19, 29, 0.96), rgba(3, 15, 23, 0.55) 78%, transparent);
  border-bottom: 1px solid rgba(0, 224, 255, 0.18);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 520px;
}
.logo {
  width: 38px;
  height: 38px;
  border: 1px solid rgba(0, 229, 255, 0.6);
  border-radius: 50%;
  position: relative;
  display: grid;
  place-items: center;
  box-shadow: 0 0 18px rgba(0, 229, 255, 0.35) inset;
}
.logo .wave {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #7ff0ff, #007a99 60%, #003446);
  box-shadow: 0 0 14px rgba(0, 229, 255, 0.8);
}
.brand-text h1 {
  font-size: 19px;
  letter-spacing: 1.2px;
  font-weight: 600;
  background: linear-gradient(90deg, #eafcff, #6fdcff 60%, #b18cff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  white-space: nowrap;
}
.brand-text .sub {
  font-size: 10.5px;
  color: #567f91;
  letter-spacing: 0.6px;
  margin-top: 2px;
  white-space: nowrap;
}

.mode-switch {
  display: flex;
  gap: 0;
  border: 1px solid rgba(0, 224, 255, 0.28);
  border-radius: 4px;
  overflow: hidden;
  margin-left: auto;
}

/* 实测数据标注：接入真实感知数据后才出现 */
.src-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-left: 10px;
  padding: 5px 10px;
  font-size: 11.5px;
  color: #9dffdc;
  background: rgba(36, 224, 160, 0.1);
  border: 1px solid rgba(36, 224, 160, 0.45);
  border-radius: 4px;
  white-space: nowrap;
}
.src-badge .dot.obs {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #24e0a0;
  box-shadow: 0 0 8px rgba(36, 224, 160, 0.9);
  animation: obsPulse 1.6s infinite;
}
@keyframes obsPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
.mode-btn {
  background: transparent;
  border: none;
  color: #6f9daf;
  font-size: 12.5px;
  padding: 8px 14px;
  cursor: pointer;
  font-family: var(--font);
  display: flex;
  align-items: center;
  gap: 7px;
  transition: all 0.2s;
}
.mode-btn + .mode-btn {
  border-left: 1px solid rgba(0, 224, 255, 0.2);
}
.mode-btn.active {
  color: #eafcff;
  background: linear-gradient(180deg, rgba(0, 190, 230, 0.32), rgba(0, 120, 160, 0.12));
  box-shadow: inset 0 -2px 0 var(--accent);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #38525e;
}
.dot.on.live {
  background: var(--ok);
  box-shadow: 0 0 8px var(--ok);
  animation: blink 1.6s infinite;
}
.dot.on.sim {
  background: var(--warn);
  box-shadow: 0 0 8px var(--warn);
}

.status {
  display: flex;
  align-items: center;
  gap: 16px;
}
.status-item {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.status-item .k {
  font-size: 10.5px;
  color: #5d8394;
}
.status-item .v {
  font-size: 15px;
  color: #cdf3ff;
}
.status-item .v small {
  font-size: 10px;
  color: #5d8394;
  margin-left: 2px;
}
.status-item .v.danger {
  color: #ff7b7b;
}
.status-item .v.warn {
  color: #ffc46b;
}
.status-item .v.ok {
  color: #63e6b6;
}
.clock {
  font-size: 12.5px;
  color: #7fc7dd;
  padding-left: 14px;
  border-left: 1px solid rgba(0, 224, 255, 0.16);
}
.sse {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 10px;
  border: 1px solid rgba(0, 224, 255, 0.28);
  color: #7fbfd2;
}
.sse i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ffb545;
}
.sse.online i {
  background: #24e0a0;
  box-shadow: 0 0 6px #24e0a0;
}
.sse.offline i {
  background: #ff5252;
}

.ticker {
  position: absolute;
  top: 74px;
  left: 0;
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  z-index: 19;
  background: linear-gradient(90deg, rgba(90, 12, 12, 0.85), rgba(60, 8, 8, 0.5) 60%, transparent);
  border-bottom: 1px solid rgba(255, 82, 82, 0.3);
  overflow: hidden;
}
.ticker-tag {
  flex: none;
  font-size: 11.5px;
  color: #ffd0d0;
  border: 1px solid rgba(255, 82, 82, 0.7);
  border-radius: 2px;
  padding: 1px 7px;
  background: rgba(255, 82, 82, 0.18);
  animation: blink 2s infinite;
}
.ticker-track {
  flex: 1;
  overflow: hidden;
}
.ticker-inner {
  display: flex;
  gap: 46px;
  width: max-content;
  animation: marquee 46s linear infinite;
}
.ticker-item {
  font-size: 12px;
  color: #ffb3b3;
  white-space: nowrap;
}
.ticker-item b {
  color: #ffdada;
}
.ticker-item.lv1 {
  color: #ffd98a;
}
.ticker-item.lv2 {
  color: #ffbc7a;
}
.ticker-item.lv3 {
  color: #ff8a8a;
}
</style>
