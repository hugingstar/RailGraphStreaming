import { useCallback, useEffect, useMemo, useState } from 'react'

import { Feed, type ConnState } from './lib/feed'
import type {
  Lang, NetworkPayload, PlanPayload, Stats, Train, TrainDetail, Alert,
} from './lib/types'
import MapView from './components/MapView'
import Sidebar, { type Filters } from './components/Sidebar'
import DetailPanel from './components/DetailPanel'
import AccountMenu from './components/AccountMenu'
import RagChat from './components/RagChat'

const EMPTY_FILTERS: Filters = {
  types: new Set(), line: '', direction: '', delayedOnly: false, query: '',
}

export default function App() {
  const feed = useMemo(() => new Feed(), [])
  const [network, setNetwork] = useState<NetworkPayload | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanPayload | null>(null)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [basemap, setBasemap] = useState(true)
  const [lang, setLang] = useState<Lang>('ko')

  // Snapshot mirrored into React state once per server tick.
  const [trains, setTrains] = useState<Train[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [detail, setDetail] = useState<TrainDetail | null>(null)
  const [conn, setConn] = useState<ConnState>('connecting')

  useEffect(() => {
    fetch('/api/network')
      .then((r) => r.json())
      .then(setNetwork)
      .catch(() => setNetwork(null))
  }, [])

  useEffect(() => {
    // The gateway already batches to one tick per second, so mirror straight
    // into state.  (Coalescing with requestAnimationFrame would stall forever
    // while the tab is hidden, since the frame callback never runs.)
    const unsub = feed.subscribe((s) => {
      setTrains([...s.trains.values()])
      setStats(s.stats)
      setAlerts(s.alerts)
      setDetail(s.detail)
      setConn(s.conn)
    })
    feed.connect()
    if (import.meta.env.DEV) {
      ;(window as unknown as Record<string, unknown>).__railgraph = feed
    }
    return () => {
      unsub()
      feed.close()
    }
  }, [feed])

  // The selected train's full route geometry and timetable.
  useEffect(() => {
    feed.select(selected)
    if (!selected) {
      setPlan(null)
      return
    }
    let live = true
    fetch(`/api/trains/${encodeURIComponent(selected)}`)
      .then((r) => r.json())
      .then((d) => {
        if (live) setPlan(d.plan ?? null)
      })
      .catch(() => {
        if (live) setPlan(null)
      })
    return () => {
      live = false
    }
  }, [feed, selected])

  const typeColors = useMemo(() => {
    const out: Record<string, string> = {}
    for (const [name, t] of Object.entries(network?.trainTypes ?? {})) out[name] = t.color
    return out
  }, [network])

  const allTypes = useMemo(() => Object.keys(network?.trainTypes ?? {}), [network])

  const lineOptions = useMemo(
    () => (network?.lines ?? []).map((l) => ({ id: l.id, name: l.name })),
    [network],
  )

  const matches = useCallback(
    (t: Train) => {
      if (filters.types.size && !filters.types.has(t.type)) return false
      if (filters.line && t.line_id !== filters.line) return false
      if (filters.direction && t.direction !== filters.direction) return false
      if (filters.delayedOnly && t.delay.p50 < 300) return false
      const q = filters.query.trim()
      if (q) {
        const hay = `${t.number} ${t.name} ${t.origin} ${t.destination}`
        if (!hay.includes(q)) return false
      }
      return true
    },
    [filters],
  )

  const listed = useMemo(
    () =>
      trains
        .filter((t) => t.status === 'RUNNING' || t.status === 'DWELL')
        .filter(matches)
        .sort((a, b) => b.delay.p50 - a.delay.p50),
    [trains, matches],
  )

  const selectedTrain = useMemo(
    () => trains.find((t) => t.train_id === selected) ?? null,
    [trains, selected],
  )

  return (
    <div className={`app ${selectedTrain ? 'with-detail' : ''}`}>
      <Sidebar
        stats={stats}
        conn={conn}
        trains={listed}
        alerts={alerts}
        selected={selected}
        filters={filters}
        allTypes={allTypes}
        lines={lineOptions}
        typeColors={typeColors}
        basemap={basemap}
        lang={lang}
        onFilters={setFilters}
        onSelect={setSelected}
        onBasemap={setBasemap}
        onLang={setLang}
      />

      <main className="stage">
        <MapView
          feed={feed}
          network={network}
          plan={plan}
          selected={selected}
          visible={matches}
          typeColors={typeColors}
          basemap={basemap}
          lang={lang}
          onSelect={setSelected}
        />
        <Legend network={network} />
        <AccountMenu />
        <RagChat />
        {conn !== 'live' && (
          <div className="banner">
            {conn === 'connecting' ? '스트림에 연결하는 중…' : '스트림이 끊겼습니다. 재연결 중…'}
          </div>
        )}
      </main>

      {selectedTrain && (
        <DetailPanel
          train={selectedTrain}
          detail={detail && detail.train_id === selectedTrain.train_id ? detail : null}
          plan={plan && plan.train_id === selectedTrain.train_id ? plan : null}
          color={typeColors[selectedTrain.type] ?? '#94a3b8'}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

function Legend({ network }: { network: NetworkPayload | null }) {
  const [open, setOpen] = useState(false)
  if (!network) return null
  return (
    <div className={`legend ${open ? 'open' : ''}`}>
      <button type="button" className="legend-title" onClick={() => setOpen((v) => !v)}>
        노선
        <i className={`legend-caret ${open ? 'open' : ''}`} />
      </button>
      {open && (
        <>
          <ul>
            {network.lines.map((l) => (
              <li key={l.id}>
                <i style={{ background: l.color, height: l.hsr ? 3 : 2 }} />
                {l.name}
              </li>
            ))}
          </ul>
          <p className="legend-note">
            열차마다 번지는 빛은 <strong>위치 확률밀도</strong>입니다. 밝을수록 그 지점에 있을
            확률이 높고, 관측이 오래될수록 넓게 퍼집니다.
          </p>
        </>
      )}
    </div>
  )
}
