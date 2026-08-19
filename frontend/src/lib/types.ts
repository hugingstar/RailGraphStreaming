export type TrainStatus = 'SCHEDULED' | 'RUNNING' | 'DWELL' | 'ARRIVED'

/** "down" runs along the pattern's canonical path (하행), "up" is the reverse (상행). */
export type Direction = 'up' | 'down'

/** Language of the place names drawn on the map. */
export type Lang = 'ko' | 'en'

export interface Quantiles {
  p05: number
  p25: number
  p50: number
  p75: number
  p95: number
  mean: number
}

export interface DelayQuantiles {
  p05: number
  p50: number
  p95: number
  mean: number
}

export interface NextStop {
  station: string
  scheduled: number
  eta_p10: number
  eta_p50: number
  eta_p90: number
}

/** One point of the along-track probability density: [lon, lat, 0..1]. */
export type BandPoint = [number, number, number]

export interface Train {
  train_id: string
  number: number
  name: string
  type: string
  line_id: string
  origin: string
  destination: string
  /** Null when the train's plan predates this field; absent on older payloads. */
  direction?: Direction | null
  ts: number
  status: TrainStatus
  km: Quantiles
  route_km: number
  delay: DelayQuantiles
  band: BandPoint[]
  lat: number
  lon: number
  spread_km: number
  confidence: number
  n_obs: number
  last_obs_ts: number | null
  progress: number
  next: NextStop | null
}

export interface TrainDetail extends Train {
  density: { lo: number; hi: number; bins: number[] }
  segments: { from: string; to: string; p: number }[]
  next_stops: NextStop[]
}

export interface Alert {
  train_id: string
  name: string
  type: string
  line_id: string
  kind: 'DELAYED' | 'RECOVERED'
  ts: number
  delay_p50: number
  delay_p95: number
  origin: string
  destination: string
  next_stop: string | null
}

export interface Stats {
  sim_ts: number
  active: number
  tracked: number
  delayed: number
  avg_delay_s: number
  max_delay_s: number
  avg_spread_km: number
  msgs_per_sec: number
  total_positions: number
  by_type: Record<string, number>
  speed: number
}

export interface Station {
  name: string
  lat: number
  lon: number
  weight: number
}

export interface Line {
  id: string
  name: string
  color: string
  hsr: boolean
  coords: [number, number][]
}

export interface NetworkPayload {
  stations: Station[]
  lines: Line[]
  trainTypes: Record<string, { color: string; hsr_kmh: number }>
}

export interface PlanPayload {
  train_id: string
  name: string
  type: string
  origin: string
  destination: string
  direction: Direction
  nodes: string[]
  cum_km: number[]
  coords: [number, number][]
  stop_idx: number[]
  timeline: [number, number, number][]
}
