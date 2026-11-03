<template>
  <div class="dashboard">
    <MapContainer />
    <TopBar />
    <LeftPanel />
    <RightPanel />
    <BottomBar />
    <CopilotDrawer />
    <ReportModal />

    <div class="toast-wrap">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.level">{{ t.text }}</div>
    </div>

    <div v-if="!store.ready" class="loading">
      <div class="radar"></div>
      <p>正在加载武汉市空间底座（20 万栋建筑 / 2.9 万条道路）…</p>
      <small v-if="store.toasts.length">{{ store.toasts[0].text }}</small>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import BottomBar from '../components/BottomBar.vue'
import CopilotDrawer from '../components/CopilotDrawer.vue'
import ReportModal from '../components/ReportModal.vue'
import LeftPanel from '../components/LeftPanel.vue'
import MapContainer from '../components/MapContainer.vue'
import RightPanel from '../components/RightPanel.vue'
import TopBar from '../components/TopBar.vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    store.pickingOrigin = false
  }
}

onMounted(() => {
  store.bootstrap()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.dashboard {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: radial-gradient(circle at 50% 30%, #071c28 0%, #04090f 62%);
}

.dashboard::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(
    0deg,
    rgba(0, 229, 255, 0.022) 0px,
    rgba(0, 229, 255, 0.022) 1px,
    transparent 1px,
    transparent 3px
  );
  z-index: 40;
  mix-blend-mode: screen;
}

.loading {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: rgba(3, 10, 16, 0.94);
  color: #9fe9ff;
  font-size: 13px;
  letter-spacing: 0.6px;
}
.loading small {
  color: #ff8f8f;
  font-size: 11.5px;
}
.radar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  border: 1px solid rgba(0, 229, 255, 0.5);
  position: relative;
  box-shadow: 0 0 24px rgba(0, 229, 255, 0.35) inset;
}
.radar::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: conic-gradient(from 0deg, rgba(0, 229, 255, 0.65), transparent 32%);
  animation: spin 1.4s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
