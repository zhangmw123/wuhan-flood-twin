import axios from 'axios'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 40000,
})

export interface StationProps {
  station_code: string
  name: string
  district: string
  station_type: 'waterlogging' | 'hydrology' | 'pump'
  current_depth_cm?: number
  rain_1h_mm?: number
  alert_level?: number
  warning_threshold_cm?: number
  danger_threshold_cm?: number
  pump_name?: string
  current_level_m?: number
  capacity_m3s?: number
  running?: boolean
}

export interface MapAction {
  action: 'FLY_TO' | 'DRAW_BUFFER' | 'SHOW_ROUTE' | 'HIGHLIGHT_SHELTER' | 'SET_RAINFALL' | 'SET_MODE'
  payload: Record<string, any>
}

export const api = {
  health: () => http.get('/health').then((r) => r.data),
  meta: () => http.get('/meta').then((r) => r.data.data),
  stations: (mode?: string, district?: string) =>
    http.get('/monitor/stations', { params: { mode, district } }).then((r) => r.data),
  alerts: () => http.get('/monitor/alerts').then((r) => r.data.data),
  districts: () => http.get('/monitor/districts').then((r) => r.data.data),
  waterLevel: () => http.get('/monitor/water-level').then((r) => r.data.data),
  weather: () => http.get('/weather').then((r) => r.data.data),
  control: (body: Record<string, any>) => http.post('/control', body).then((r) => r.data.data),
  layer: (kind: string, bbox?: number[], limit = 20000) =>
    http.get(`/layers/${kind}`, { params: { bbox: bbox?.join(','), limit } }).then((r) => r.data),
  layerStatic: (kind: string) => http.get(`/layers/${kind}`).then((r) => r.data),
  impact: (body: Record<string, any>) =>
    http.post('/analysis/inundation-impact', body).then((r) => r.data),
  route: (body: Record<string, any>) =>
    http.post('/analysis/evacuation-route', body).then((r) => r.data),
  buffer: (lng: number, lat: number, radius: number) =>
    http.get('/layers/buffer', { params: { lng, lat, radius_m: radius } }).then((r) => r.data.data),
  copilot: (prompt: string) => http.post('/copilot/chat', { prompt }).then((r) => r.data),
  briefing: (rainfall_mm: number, duration_hours: number, district?: string | null) =>
    http
      .get('/report/briefing', { params: { rainfall_mm, duration_hours, district: district || undefined } })
      .then((r) => r.data.data as BriefingResult),
}

export interface BriefingResult {
  markdown: string
  filename: string
  generated_at: string
  query: { rainfall_mm: number; duration_hours: number; district: string | null }
  summary: {
    zone_count: number
    inundated_area_km2: number
    affected_building_count: number
    blocked_road_count: number
    max_depth_cm: number
    threatened_shelter_count: number
  }
}

export function openAlertStream(handlers: {
  onOpen?: () => void
  onMessage: (data: any) => void
  onError?: () => void
}) {
  const base = import.meta.env.VITE_API_BASE || '/api/v1'
  const es = new EventSource(`${base}/stream/alerts`)
  es.addEventListener('flood_alert', (ev: MessageEvent) => {
    try {
      handlers.onMessage(JSON.parse(ev.data))
    } catch (e) {
      /* ignore malformed frame */
    }
  })
  es.onopen = () => handlers.onOpen?.()
  es.onerror = () => handlers.onError?.()
  return es
}
