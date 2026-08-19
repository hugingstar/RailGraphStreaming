import type { Alert, Stats, Train, TrainDetail } from './types'

export type ConnState = 'connecting' | 'live' | 'down'

export interface FeedSnapshot {
  trains: Map<string, Train>
  alerts: Alert[]
  stats: Stats | null
  detail: TrainDetail | null
  conn: ConnState
  /** Bumped on every server tick so consumers can cheaply detect freshness. */
  rev: number
}

type Listener = (s: FeedSnapshot) => void

/**
 * WebSocket client for the gateway.
 *
 * Holds the world in a mutable Map and notifies subscribers once per server
 * tick.  React components that render lists subscribe directly; the map layer
 * subscribes too but writes into MapLibre imperatively, so a hundred moving
 * trains never touch the React reconciler.
 */
export class Feed {
  private ws: WebSocket | null = null
  private listeners = new Set<Listener>()
  private retry = 0
  private closed = false

  readonly snapshot: FeedSnapshot = {
    trains: new Map(),
    alerts: [],
    stats: null,
    detail: null,
    conn: 'connecting',
    rev: 0,
  }

  private selected: string | null = null

  /**
   * Idempotent and re-entrant: React StrictMode mounts, tears down and remounts
   * effects in development, so `close()` has to be recoverable rather than
   * final, and a second connect must not open a second socket.
   */
  connect() {
    this.closed = false
    const state = this.ws?.readyState
    if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws = ws

    ws.onopen = () => {
      if (this.ws !== ws) return
      this.retry = 0
      this.snapshot.conn = 'live'
      if (this.selected) this.send({ t: 'select', train_id: this.selected })
    }

    ws.onmessage = async (ev) => {
      if (this.ws !== ws) return
      const raw = ev.data instanceof Blob ? await ev.data.text() : (ev.data as string)
      const msg = JSON.parse(raw)
      if (msg.t === 'hello' || msg.t === 'tick') this.ingest(msg)
    }

    ws.onclose = () => {
      if (this.ws !== ws) return          // superseded by a newer socket
      this.snapshot.conn = 'down'
      this.emit()
      if (this.closed) return
      this.retry = Math.min(this.retry + 1, 6)
      setTimeout(() => this.connect(), 400 * 2 ** (this.retry - 1))
    }

    ws.onerror = () => ws.close()
  }

  private ingest(msg: {
    trains?: Train[]
    removed?: string[]
    alerts?: Alert[]
    stats?: Stats
    detail?: TrainDetail
  }) {
    const s = this.snapshot
    if (msg.trains) for (const t of msg.trains) s.trains.set(t.train_id, t)
    if (msg.removed) for (const id of msg.removed) s.trains.delete(id)
    if (msg.alerts?.length) s.alerts = [...msg.alerts.slice().reverse(), ...s.alerts].slice(0, 40)
    if (msg.stats) s.stats = msg.stats
    s.detail = msg.detail ?? (this.selected ? s.detail : null)
    s.conn = 'live'
    s.rev++
    this.emit()
  }

  select(trainId: string | null) {
    this.selected = trainId
    if (!trainId) this.snapshot.detail = null
    this.send({ t: 'select', train_id: trainId })
  }

  private send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload))
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit() {
    for (const fn of this.listeners) fn(this.snapshot)
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
