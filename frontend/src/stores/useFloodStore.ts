import { defineStore } from 'pinia'
import { api, openAlertStream, type MapAction, type StationProps } from '../api/floodApi'

interface StationFeature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: StationProps
}

export interface Message {
  role: 'user' | 'assistant'
  text: string
  provider?: string
  time: string
}

let sse: EventSource | null = null
let pollTimer: number | null = null

export const useFloodStore = defineStore('flood', {
  state: () => ({
    ready: false,
    meta: null as any,
    mode: 'simulation' as 'realtime' | 'simulation',
    hour: 8,
    playing: true,
    stationMeta: null as any,
    stations: [] as StationFeature[],
    stats: [] as any[],
    waterLevel: null as any,
    weather: null as any,
    alerts: [] as any[],
    newAlertCount: 0,
    selected: null as StationFeature | null,
    // 推演
    rainfall: 120,
    duration: 3,
    impact: null as any,
    impactLoading: false,
    briefingLoading: false,
    reportPreview: null as { filename: string; rainfall: number; duration: number } | null,
    // 路径
    route: null as any,
    pickingOrigin: false,
    routeLoading: false,
    // 缓冲
    buffer: null as any,
    // 图层开关
    layers: {
      buildings: true,
      roads: true,
      water: true,
      districts: true,
      stations: true,
      flood: true,
      shelters: false,
    },
    // 地图指令队列
    commands: [] as { id: number; action: string; payload: any }[],
    cmdSeq: 0,
    // 助手
    copilotOpen: false,
    messages: [] as Message[],
    copilotBusy: false,
    sseStatus: 'connecting' as 'connecting' | 'online' | 'offline',
    toasts: [] as { id: number; text: string; level: string }[],
    toastSeq: 0,
  }),

  getters: {
    dangerStations: (s) =>
      s.stations
        .filter((f) => (f.properties.current_depth_cm || 0) >= 50)
        .sort((a, b) => (b.properties.current_depth_cm || 0) - (a.properties.current_depth_cm || 0)),
    alertStations: (s) =>
      s.stations
        .filter((f) => (f.properties.current_depth_cm || 0) >= 30)
        .sort((a, b) => (b.properties.current_depth_cm || 0) - (a.properties.current_depth_cm || 0)),
    topStations: (s) =>
      s.stations
        .filter((f) => f.properties.station_type === 'waterlogging')
        .sort((a, b) => (b.properties.current_depth_cm || 0) - (a.properties.current_depth_cm || 0))
        .slice(0, 10),
    hankou: (s) => s.stations.find((f) => (f.properties.name || '').includes('汉口'))?.properties,
  },

  actions: {
    toast(text: string, level = 'info') {
      const id = ++this.toastSeq
      this.toasts.push({ id, text, level })
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id)
      }, 4200)
    },

    pushCommand(action: string, payload: any) {
      this.commands.push({ id: ++this.cmdSeq, action, payload })
    },

    async bootstrap() {
      try {
        this.meta = await api.meta()
        this.mode = this.meta.status?.mode || 'simulation'
        this.hour = this.meta.status?.hour ?? 8
        await this.refreshStations()
        await Promise.all([this.refreshStats(), this.refreshWaterLevel(), this.refreshWeather()])
        this.ready = true
        this.startStream()
        this.startPolling()
      } catch (e: any) {
        this.toast(`后端未就绪：${e?.message || e}`, 'error')
      }
    },

    async refreshStations() {
      const fc = await api.stations()
      this.stations = fc.features
      this.stationMeta = fc.metadata
      if (fc.metadata) {
        this.hour = fc.metadata.hour
        this.mode = fc.metadata.mode
      }
    },

    async refreshStats() {
      this.stats = await api.districts()
    },

    async refreshWaterLevel() {
      this.waterLevel = await api.waterLevel()
    },

    async refreshWeather() {
      try {
        this.weather = await api.weather()
      } catch {
        this.weather = null
      }
    },

    startStream() {
      if (sse) sse.close()
      sse = openAlertStream({
        onOpen: () => (this.sseStatus = 'online'),
        onError: () => (this.sseStatus = 'offline'),
        onMessage: (payload) => {
          this.alerts = payload.alerts || []
          this.newAlertCount = payload.new_alert_codes?.length || 0
          if (payload.hour != null) this.hour = payload.hour
          const fresh = payload.new_alert_codes || []
          if (fresh.length) {
            const first = this.alerts.find((a: any) => a.station_code === fresh[0])
            if (first) this.toast(first.message, first.alert_level >= 3 ? 'error' : 'warn')
          }
        },
      })
    },

    startPolling() {
      if (pollTimer) window.clearInterval(pollTimer)
      pollTimer = window.setInterval(async () => {
        try {
          await this.refreshStations()
          if (this.mode === 'simulation') await this.refreshStats()
        } catch {
          /* 静默重试 */
        }
      }, 4000)
    },

    async setMode(mode: 'realtime' | 'simulation') {
      this.mode = mode
      await api.control({ mode })
      await this.refreshStations()
      await this.refreshStats()
      this.toast(mode === 'simulation' ? '已切换至 2016·7·6 特大暴雨推演模式' : '已接入 Open-Meteo 实时气象', 'ok')
    },

    async seek(hour: number) {
      this.hour = hour
      await api.control({ hour })
      await this.refreshStations()
      await this.refreshStats()
    },

    async togglePlay() {
      this.playing = !this.playing
      await api.control({ playing: this.playing })
    },

    async runImpact() {
      this.impactLoading = true
      try {
        const res = await api.impact({
          rainfall_mm: this.rainfall,
          duration_hours: this.duration,
          target_district: null,
        })
        this.impact = res.data
        this.pushCommand('SHOW_FLOOD', { geojson: res.data.inundation_polygons_geojson })
        this.toast(`推演完成：淹没 ${res.data.inundated_area_km2} km²，受淹建筑 ${res.data.affected_building_count} 栋`, 'ok')
      } catch (e: any) {
        this.toast(`推演失败：${e?.message || e}`, 'error')
      } finally {
        this.impactLoading = false
      }
    },

    clearImpact() {
      this.impact = null
      this.pushCommand('SHOW_FLOOD', { geojson: null })
    },

    /** 生成防汛会商简报并打开预览（预览窗内可下载 Markdown / 打印 PDF）。 */
    async openBriefing() {
      this.briefingLoading = true
      try {
        const r = await api.briefing(this.rainfall, this.duration)
        this.reportPreview = {
          filename: r.filename,
          rainfall: this.rainfall,
          duration: this.duration,
        }
        this.toast(
          `简报已生成：淹没 ${r.summary.inundated_area_km2} km² / 受淹建筑 ${r.summary.affected_building_count} 栋 / 阻断 ${r.summary.blocked_road_count} 段`,
          'ok',
        )
      } catch (e: any) {
        this.toast(`简报生成失败：${e?.message || e}`, 'error')
      } finally {
        this.briefingLoading = false
      }
    },

    async planRoute(lng: number, lat: number) {
      this.routeLoading = true
      this.pickingOrigin = false
      try {
        const res = await api.route({ origin_lng: lng, origin_lat: lat, avoid_waterlogging: true })
        if (res.code !== 200) {
          this.toast(res.message || '未找到可用避难所', 'warn')
          return
        }
        this.route = res.data
        this.pushCommand('SHOW_ROUTE', res.data)
        this.toast(
          `已规划至【${res.data.target_shelter.name}】的路线，${res.data.target_shelter.distance_m} 米`,
          'ok',
        )
      } catch (e: any) {
        this.toast(`路径规划失败：${e?.message || e}`, 'error')
      } finally {
        this.routeLoading = false
      }
    },

    startPickOrigin() {
      this.pickingOrigin = true
      this.toast('请在地图上点选受灾点位（Esc 取消）', 'info')
    },

    async askCopilot(prompt: string) {
      const text = prompt.trim()
      if (!text || this.copilotBusy) return
      this.messages.push({ role: 'user', text, time: nowText() })
      this.copilotBusy = true
      try {
        const res = await api.copilot(text)
        this.messages.push({
          role: 'assistant',
          text: res.reply || '（无回复）',
          provider: res.provider,
          time: nowText(),
        })
        ;(res.map_actions || []).forEach((a: MapAction) => this.applyAction(a))
      } catch (e: any) {
        this.messages.push({ role: 'assistant', text: `助手服务异常：${e?.message || e}`, time: nowText() })
      } finally {
        this.copilotBusy = false
      }
    },

    applyAction(a: MapAction) {
      switch (a.action) {
        case 'FLY_TO':
          this.pushCommand('FLY_TO', a.payload)
          break
        case 'DRAW_BUFFER':
          this.buffer = a.payload
          this.pushCommand('DRAW_BUFFER', a.payload)
          break
        case 'SHOW_ROUTE':
          this.route = a.payload
          this.pushCommand('SHOW_ROUTE', a.payload)
          break
        case 'HIGHLIGHT_SHELTER':
          this.layers.shelters = true
          this.pushCommand('HIGHLIGHT_SHELTER', a.payload)
          break
        case 'SET_RAINFALL':
          this.rainfall = a.payload.rainfall_mm
          this.duration = a.payload.duration_h || this.duration
          this.runImpact()
          break
        case 'SET_MODE':
          this.setMode(a.payload.mode)
          break
      }
    },

    ackCommand(id: number) {
      this.commands = this.commands.filter((c) => c.id !== id)
    },
  },
})

function nowText() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
