<template>
  <div ref="el" class="echart"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ option: any; height?: string }>()
const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null

onMounted(() => {
  chart = echarts.init(el.value!, undefined, { renderer: 'canvas' })
  chart.setOption(props.option)
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value!)
})

watch(
  () => props.option,
  (o) => chart?.setOption(o, true),
  { deep: true },
)

onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
})
</script>

<style scoped>
.echart {
  width: 100%;
  height: 100%;
  min-height: 90px;
}
</style>
