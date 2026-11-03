<template>
  <div v-if="store.reportPreview" class="report-mask" @click.self="close">
    <div class="report-box">
      <header class="report-head">
        <div class="titles">
          <span class="kicker">防汛会商材料</span>
          <h2>武汉市防汛会商简报</h2>
        </div>
        <div class="acts">
          <button class="btn ghost tiny" @click="download">下载 Markdown</button>
          <button class="btn primary tiny" @click="print">打印 / 存为 PDF</button>
          <button class="close" title="关闭 (Esc)" @click="close">✕</button>
        </div>
      </header>

      <iframe
        v-if="src"
        :src="src"
        class="report-frame"
        title="防汛会商简报预览"
        sandbox="allow-same-origin allow-modals"
      ></iframe>

      <footer class="report-foot">
        <span class="hint">
          数据口径：情景推演（{{ store.rainfall }}mm / {{ store.duration }}h），
          推演成分已在正文第六节声明
        </span>
        <span class="file mono">{{ store.reportPreview.filename }}</span>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()

const apiBase = import.meta.env.VITE_API_BASE || '/api/v1'

const src = computed(() => {
  if (!store.reportPreview) return ''
  const { rainfall, duration } = store.reportPreview
  return `${apiBase}/report/briefing?rainfall_mm=${rainfall}&duration_hours=${duration}&fmt=html&_t=${Date.now()}`
})

function close() {
  store.reportPreview = null
}

function download() {
  if (!store.reportPreview) return
  const { rainfall, duration } = store.reportPreview
  const a = document.createElement('a')
  a.href = `${apiBase}/report/briefing?rainfall_mm=${rainfall}&duration_hours=${duration}&fmt=md`
  a.download = store.reportPreview.filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function print() {
  const frame = document.querySelector<HTMLIFrameElement>('.report-frame')
  frame?.contentWindow?.print()
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.reportPreview) close()
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.report-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(2, 8, 14, 0.82);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
}

.report-box {
  width: min(980px, 96vw);
  height: min(88vh, 1020px);
  display: flex;
  flex-direction: column;
  background: #0c1620;
  border: 1px solid rgba(255, 196, 92, 0.32);
  border-radius: 6px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.6), 0 0 40px rgba(255, 180, 60, 0.08);
  overflow: hidden;
}

.report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 196, 92, 0.2);
  background: linear-gradient(90deg, rgba(120, 80, 15, 0.28), transparent);
}
.titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kicker {
  font-size: 10px;
  letter-spacing: 2px;
  color: #c99b45;
}
.titles h2 {
  margin: 0;
  font-size: 16px;
  color: #ffe6b8;
  letter-spacing: 1px;
}
.acts {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btn.tiny {
  padding: 4px 10px;
  font-size: 11px;
}
.btn.primary {
  border-color: rgba(255, 196, 92, 0.7);
  background: linear-gradient(90deg, rgba(180, 120, 20, 0.4), rgba(255, 196, 92, 0.22));
  color: #ffe9c4;
}
.close {
  margin-left: 4px;
  width: 26px;
  height: 26px;
  border-radius: 3px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.04);
  color: #9fb6c4;
  cursor: pointer;
  font-size: 12px;
}
.close:hover {
  color: #fff;
  border-color: rgba(255, 120, 120, 0.6);
}

.report-frame {
  flex: 1;
  width: 100%;
  border: 0;
  background: #fff;
}

.report-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-top: 1px solid rgba(255, 196, 92, 0.14);
  font-size: 10.5px;
  color: #6f8b9c;
}
.file {
  color: #8aa7b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 46%;
}
</style>
