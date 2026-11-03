<template>
  <transition name="slide">
    <section v-if="store.copilotOpen" class="copilot panel">
      <div class="panel-head">
        <span class="title">AI 防汛调度 Copilot</span>
        <div class="head-right">
          <span class="chip" :class="store.meta?.llm_enabled ? 'info' : 'ok'">
            {{ store.meta?.llm_enabled ? 'LLM Function Calling' : '本地规则引擎' }}
          </span>
          <button class="close" @click="store.copilotOpen = false">×</button>
        </div>
      </div>

      <div class="messages" ref="listEl">
        <div v-if="!store.messages.length" class="intro">
          <p class="intro-title">用自然语言指挥地图</p>
          <p class="muted">支持空间查询、镜头定位、缓冲区分析、避险路径与灾情推演，例如：</p>
          <div class="prompts">
            <button v-for="p in prompts" :key="p" class="prompt" @click="ask(p)">{{ p }}</button>
          </div>
        </div>
        <div v-for="(m, i) in store.messages" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div class="text">{{ m.text }}</div>
            <div class="meta">
              <span>{{ m.time }}</span>
              <span v-if="m.provider" class="mono">{{ m.provider }}</span>
            </div>
          </div>
        </div>
        <div v-if="store.copilotBusy" class="msg assistant">
          <div class="bubble thinking"><i></i><i></i><i></i> 正在检索空间数据…</div>
        </div>
      </div>

      <div class="input-row">
        <input
          v-model="draft"
          placeholder="例如：武昌区积水超过 40cm 的点位，飞过去并做 500 米缓冲分析"
          @keydown.enter="ask(draft)"
        />
        <button class="btn" :disabled="store.copilotBusy || !draft.trim()" @click="ask(draft)">发送</button>
      </div>
    </section>
  </transition>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useFloodStore } from '../stores/useFloodStore'

const store = useFloodStore()
const draft = ref('')
const listEl = ref<HTMLDivElement | null>(null)

const prompts = [
  '筛选出武昌区积水超过 40cm 的渍水点并飞过去',
  '在光谷广场下穿通道周边 500 米做受灾影响分析',
  '为当前积水最严重的点位规划最近避难所路线',
  '按 200mm / 6h 推演全城内涝灾情',
]

function ask(text: string) {
  const t = (text || '').trim()
  if (!t) return
  draft.value = ''
  store.askCopilot(t)
}

watch(
  () => store.messages.length,
  async () => {
    await nextTick()
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' })
  },
)
watch(
  () => store.copilotBusy,
  async () => {
    await nextTick()
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' })
  },
)
</script>

<style scoped>
.copilot {
  position: absolute;
  right: 16px;
  bottom: 152px;
  width: 396px;
  height: 460px;
  z-index: 30;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.close {
  background: transparent;
  border: none;
  color: #6b93a5;
  font-size: 18px;
  cursor: pointer;
  line-height: 1;
}
.close:hover {
  color: #eafcff;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.intro-title {
  font-size: 13px;
  color: #b6ffe4;
  margin-bottom: 4px;
}
.intro .muted {
  font-size: 11.5px;
  line-height: 1.6;
}
.prompts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 9px;
}
.prompt {
  text-align: left;
  font-size: 11.5px;
  color: #9fe9ff;
  background: rgba(0, 224, 255, 0.07);
  border: 1px dashed rgba(0, 224, 255, 0.35);
  border-radius: 3px;
  padding: 6px 9px;
  cursor: pointer;
  font-family: var(--font);
  transition: all 0.18s;
}
.prompt:hover {
  background: rgba(0, 224, 255, 0.16);
  border-style: solid;
}
.msg {
  display: flex;
}
.msg.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 88%;
  padding: 7px 10px;
  border-radius: 5px;
  font-size: 12.5px;
  line-height: 1.7;
}
.msg.user .bubble {
  background: linear-gradient(135deg, rgba(0, 160, 200, 0.35), rgba(0, 100, 140, 0.28));
  border: 1px solid rgba(0, 229, 255, 0.35);
  color: #eafcff;
}
.msg.assistant .bubble {
  background: rgba(9, 32, 45, 0.92);
  border: 1px solid rgba(0, 224, 255, 0.18);
  color: #cbeaf6;
}
.meta {
  display: flex;
  gap: 8px;
  font-size: 9.5px;
  color: #4f7386;
  margin-top: 3px;
}
.thinking {
  color: #7fa9bb;
  font-size: 11.5px;
}
.thinking i {
  display: inline-block;
  width: 4px;
  height: 4px;
  margin-right: 3px;
  border-radius: 50%;
  background: #00e5ff;
  animation: blink 1.2s infinite;
}
.thinking i:nth-child(2) {
  animation-delay: 0.2s;
}
.thinking i:nth-child(3) {
  animation-delay: 0.4s;
}
.input-row {
  display: flex;
  gap: 8px;
  padding: 9px 11px;
  border-top: 1px solid rgba(0, 224, 255, 0.12);
}
.input-row input {
  flex: 1;
  min-width: 0;
  background: rgba(0, 30, 44, 0.7);
  border: 1px solid rgba(0, 224, 255, 0.22);
  border-radius: 3px;
  color: #dff7ff;
  font-size: 12px;
  padding: 7px 9px;
  outline: none;
  font-family: var(--font);
}
.input-row input:focus {
  border-color: rgba(0, 229, 255, 0.6);
  box-shadow: 0 0 12px rgba(0, 229, 255, 0.2);
}
.slide-enter-active,
.slide-leave-active {
  transition: all 0.24s ease;
}
.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateX(24px);
}
</style>
