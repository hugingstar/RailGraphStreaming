import { Link } from 'react-router-dom'

import type { Alert, Direction, Lang, Stats, Train } from '../lib/types'
import type { ConnState } from '../lib/feed'
import { delayShort, delayTone, hhmm, hhmmss } from '../lib/format'

export interface Filters {
  types: Set<string>
  line: string
  /** '' means both directions. */
  direction: Direction | ''
  delayedOnly: boolean
  query: string
}

interface Props {
  stats: Stats | null
  conn: ConnState
  trains: Train[]
  alerts: Alert[]
  selected: string | null
  filters: Filters
  allTypes: string[]
  lines: { id: string; name: string }[]
  typeColors: Record<string, string>
  basemap: boolean
  lang: Lang
  onFilters: (f: Filters) => void
  onSelect: (id: string) => void
  onBasemap: (v: boolean) => void
  onLang: (v: Lang) => void
}

/** Direction filter choices, in the order they appear in the segmented control. */
const DIRECTIONS: readonly (readonly [Direction | '', string])[] = [
  ['', '전체'],
  ['down', '하행'],
  ['up', '상행'],
]

const CONN_LABEL: Record<ConnState, string> = {
  connecting: '연결 중',
  live: '실시간',
  down: '끊김',
}

export default function Sidebar(p: Props) {
  const { stats, conn, trains, alerts, selected, filters, allTypes, lines, typeColors } = p

  const toggleType = (t: string) => {
    const next = new Set(filters.types)
    if (next.has(t)) next.delete(t)
    else next.add(t)
    p.onFilters({ ...filters, types: next })
  }

  return (
    <aside className="sidebar">
      <header className="brand">
        <div className="brand-row">
          <h1><Link to="/">🕊️ 비둘기 역장님</Link></h1>
          <span className={`conn conn-${conn}`}>
            <i /> {CONN_LABEL[conn]}
          </span>
        </div>
        <p className="tagline">
          전국 열차가 지금 <strong>어디쯤 있는지</strong> 알려드려요
        </p>
        <div className="clock">
          <span className="clock-time">{stats ? hhmmss(stats.sim_ts) : '--:--:--'}</span>
          <span className="dim">KST</span>
          {stats && stats.speed !== 1 && <span className="speed">×{stats.speed}</span>}
        </div>
      </header>

      <div className="kpis">
        <div className="kpi">
          <b>{stats?.active ?? 0}</b>
          <label>운행 중</label>
        </div>
        <div className="kpi">
          <b className={stats && stats.delayed > 0 ? 'tone-major' : undefined}>{stats?.delayed ?? 0}</b>
          <label>5분↑ 지연</label>
        </div>
        <div className="kpi">
          <b>{stats ? (stats.avg_delay_s / 60).toFixed(1) : '0.0'}<em>분</em></b>
          <label>평균 지연</label>
        </div>
        <div className="kpi">
          <b>{stats ? stats.avg_spread_km.toFixed(1) : '0.0'}<em>km</em></b>
          <label>90% 구간폭</label>
        </div>
      </div>

      <div className="throughput">
        <span>실시간 수신</span>
        <i className="bar">
          <i style={{ width: `${Math.min(100, ((stats?.msgs_per_sec ?? 0) / 200) * 100)}%` }} />
        </i>
        <span className="dim">초당 {(stats?.msgs_per_sec ?? 0).toFixed(0)}건</span>
      </div>

      <div className="filters">
        <div className="chips-head">
          <span className="chips-title">차종</span>
          <div className="chips-actions">
            <button type="button" className="chips-toggle" onClick={() => p.onFilters({ ...filters, types: new Set() })}>
              전체 선택
            </button>
            <em />
            <button type="button" className="chips-toggle" onClick={() => p.onFilters({ ...filters, types: new Set(['__none__']) })}>
              전체 해제
            </button>
          </div>
        </div>
        <div className="chips">
          {allTypes.map((t) => (
            <button
              key={t}
              className={`chip ${filters.types.size === 0 || filters.types.has(t) ? 'on' : ''}`}
              style={{ ['--chip' as string]: typeColors[t] ?? '#94a3b8' }}
              onClick={() => toggleType(t)}
            >
              <i /> {t}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="운행 방향">
          {DIRECTIONS.map(([value, label]) => (
            <button
              key={value || 'all'}
              className={filters.direction === value ? 'on' : ''}
              onClick={() => p.onFilters({ ...filters, direction: value })}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="filter-row">
          <select value={filters.line} onChange={(e) => p.onFilters({ ...filters, line: e.target.value })}>
            <option value="">전 노선</option>
            {lines.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
          <label className="switch">
            <input
              type="checkbox"
              checked={filters.delayedOnly}
              onChange={(e) => p.onFilters({ ...filters, delayedOnly: e.target.checked })}
            />
            지연만
          </label>
          <label className="switch">
            <input type="checkbox" checked={p.basemap} onChange={(e) => p.onBasemap(e.target.checked)} />
            지도
          </label>
        </div>
        <div className="lang-row">
          <span className="filter-label">지도 글자</span>
          <div className="lang-grid" role="group" aria-label="지도 글자 언어">
            <button className={p.lang === 'ko' ? 'on' : ''} onClick={() => p.onLang('ko')}>
              한글
            </button>
            <button className={p.lang === 'en' ? 'on' : ''} onClick={() => p.onLang('en')}>
              English
            </button>
            <button className={p.lang === 'ja' ? 'on' : ''} onClick={() => p.onLang('ja')}>
              日本語
            </button>
            <button className={p.lang === 'zh' ? 'on' : ''} onClick={() => p.onLang('zh')}>
              中文
            </button>
          </div>
        </div>
        <input
          className="search"
          placeholder="열차번호 · 역 이름 검색"
          value={filters.query}
          onChange={(e) => p.onFilters({ ...filters, query: e.target.value })}
        />
      </div>

      {alerts.length > 0 && (
        <div className="alerts">
          {alerts.slice(0, 3).map((a, i) => (
            <button key={`${a.train_id}-${a.ts}-${i}`} className={`alert ${a.kind.toLowerCase()}`}
                    onClick={() => p.onSelect(a.train_id)}>
              <span className="alert-time">{hhmm(a.ts)}</span>
              <span className="alert-body">
                <b>{a.name}</b>{' '}
                {a.kind === 'DELAYED'
                  ? `${Math.round(a.delay_p50 / 60)}분 지연`
                  : '지연 회복'}
                {a.next_stop && <em> · 다음 {a.next_stop}</em>}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="list-head">
        <span>열차 {trains.length}</span>
        <span className="dim">지연 순</span>
      </div>

      <ul className="train-list">
        {trains.map((t) => (
          <li key={t.train_id}>
            <button
              className={`train ${t.train_id === selected ? 'sel' : ''}`}
              onClick={() => p.onSelect(t.train_id)}
            >
              <i className="train-type" style={{ background: typeColors[t.type] ?? '#94a3b8' }} />
              <span className="train-no">{t.number}</span>
              {t.direction ? (
                <span className={`train-dir dir-${t.direction}`}>
                  {t.direction === 'up' ? '상' : '하'}
                </span>
              ) : (
                <i />
              )}
              <span className="train-od">
                {t.origin}<i>→</i>{t.destination}
              </span>
              <span className={`train-delay tone-${delayTone(t.delay.p50)}`}>{delayShort(t.delay.p50)}</span>
              <span className="train-prog">
                <i style={{ width: `${t.progress * 100}%` }} />
              </span>
            </button>
          </li>
        ))}
        {trains.length === 0 && <li className="empty">조건에 맞는 열차가 없습니다</li>}
      </ul>
    </aside>
  )
}
