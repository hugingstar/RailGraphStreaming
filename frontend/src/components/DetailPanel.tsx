import type { PlanPayload, Train, TrainDetail } from '../lib/types'
import { delayLabel, delayTone, hhmm, km, pct } from '../lib/format'
import { DelayGauge, DensityChart, SegmentBars } from './Charts'

interface Props {
  train: Train
  detail: TrainDetail | null
  plan: PlanPayload | null
  color: string
  onClose: () => void
}

/** Signed minutes, as a passenger reads them: "+7분" late, "-2분" early. */
const signedMin = (seconds: number) => {
  const m = Math.round(seconds / 60)
  return `${m > 0 ? '+' : m < 0 ? '−' : '±'}${Math.abs(m)}분`
}

export default function DetailPanel({ train, detail, plan, color, onClose }: Props) {
  const stops = plan?.stop_idx ?? []
  const passed = train.km.p50

  // The delay posterior is measured against the schedule, so its 90% band is
  // exactly "how much later or earlier than an undelayed run this train is" --
  // the time-domain twin of the ±km position spread.
  const timeSpreadMin = (train.delay.p95 - train.delay.p05) / 120

  return (
    <aside className="detail">
      <header>
        <div className="detail-title">
          <span className="badge" style={{ background: color }}>{train.type}</span>
          <h2>{train.number}</h2>
          <button className="close" onClick={onClose} aria-label="닫기">×</button>
        </div>
        <p className="route">
          {train.origin} <i>→</i> {train.destination}
          {train.direction && (
            <span className={`train-dir dir-${train.direction}`}>
              {train.direction === 'up' ? '상행' : '하행'}
            </span>
          )}
        </p>
      </header>

      <div className="detail-kpis">
        <div>
          <label>추정 지연</label>
          <b className={`tone-${delayTone(train.delay.p50)}`}>{delayLabel(train.delay.p50)}</b>
        </div>
        <div>
          <label>위치 불확실성</label>
          <b>±{(train.spread_km / 2).toFixed(1)}km</b>
        </div>
        <div className="kpi-wide">
          <label>
            시간 불확실성 <span className="dim">정시 운행 대비 · 90% 구간</span>
          </label>
          <b>±{timeSpreadMin.toFixed(1)}분</b>
          <span className="kpi-range">
            {signedMin(train.delay.p05)} ~ {signedMin(train.delay.p95)}
          </span>
        </div>
        <div>
          <label>신뢰도</label>
          <b>{pct(train.confidence)}</b>
        </div>
        <div>
          <label>관측 반영</label>
          <b>{train.n_obs}회</b>
        </div>
      </div>

      <div className="progress">
        <div className="progress-track">
          <i className="progress-fill" style={{ width: `${train.progress * 100}%`, background: color }} />
          <i
            className="progress-band"
            style={{
              left: `${(train.km.p05 / train.route_km) * 100}%`,
              width: `${((train.km.p95 - train.km.p05) / train.route_km) * 100}%`,
            }}
          />
        </div>
        <div className="progress-meta">
          <span>{km(passed)} 주행</span>
          <span>전체 {km(train.route_km)}</span>
        </div>
      </div>

      {detail && <DensityChart detail={detail} />}
      {detail && <DelayGauge detail={detail} />}
      {detail && <SegmentBars detail={detail} />}

      {plan && (
        <div className="timetable">
          <h4>정차역 · 도착 예측 <span className="dim">중앙값 · 5–95%</span></h4>
          <ol>
            {stops.map((i) => {
              const name = plan.nodes[i]
              const sched = plan.timeline[i][0]
              const done = plan.cum_km[i] <= passed
              // Arrival time is just the scheduled time shifted by the delay,
              // so the posterior over delay carries straight over to every ETA.
              return (
                <li key={`${name}-${i}`} className={done ? 'done' : ''}>
                  <i className="dot" style={done ? { background: color, borderColor: color } : undefined} />
                  <span className="stop-name">{name}</span>
                  <span className="stop-sched">{hhmm(sched)}</span>
                  <span className="stop-eta">
                    {done ? (
                      <em>통과</em>
                    ) : (
                      <>
                        {hhmm(sched + train.delay.p50)}
                        <em className="dim">
                          {' '}
                          {hhmm(sched + train.delay.p05)}–{hhmm(sched + train.delay.p95)}
                        </em>
                      </>
                    )}
                  </span>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </aside>
  )
}
