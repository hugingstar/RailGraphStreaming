import type { TrainDetail } from '../lib/types'
import { km } from '../lib/format'

/**
 * The along-track posterior, drawn as an area chart over route offset.
 * This is the same array the map paints as a glowing smear, shown straight.
 */
export function DensityChart({ detail }: { detail: TrainDetail }) {
  const bins = detail.density.bins
  const { lo, hi } = detail.density
  const W = 320
  const H = 88
  const n = bins.length
  if (!n) return null

  const x = (i: number) => (i / (n - 1)) * W
  const y = (v: number) => H - v * (H - 10) - 2
  const path =
    `M0,${H} ` + bins.map((v, i) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ') + ` L${W},${H} Z`
  const stroke = bins.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')

  const at = (v: number) => ((v - lo) / Math.max(hi - lo, 1e-6)) * W
  const p50 = at(detail.km.p50)

  return (
    <figure className="chart">
      <figcaption>
        노선상 위치 확률밀도
        <span className="dim">{km(lo)} – {km(hi)}</span>
      </figcaption>
      <p className="chart-hint">열차가 이 구간 어디쯤 있을지, 그 확률을 위치별로 나타낸 분포입니다.</p>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label="열차 위치의 확률밀도">
        <defs>
          <linearGradient id="dens" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.75" />
            <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        <rect x={at(detail.km.p05)} y="0" width={Math.max(at(detail.km.p95) - at(detail.km.p05), 1)}
              height={H} fill="#38bdf8" opacity="0.07" />
        <path d={path} fill="url(#dens)" />
        <path d={stroke} fill="none" stroke="#7dd3fc" strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
        <line x1={p50} x2={p50} y1="0" y2={H} stroke="#f8fafc" strokeWidth="1"
              strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="chart-axis">
        <span>{km(lo)}</span>
        <span className="mid">중앙값 {km(detail.km.p50)}</span>
        <span>{km(hi)}</span>
      </div>
    </figure>
  )
}

/** Which inter-station stretch is the train on, and with what probability. */
export function SegmentBars({ detail }: { detail: TrainDetail }) {
  if (!detail.segments.length) return null
  return (
    <div className="segments">
      <h4>구간별 확률</h4>
      {detail.segments.map((s) => (
        <div className="seg-row" key={`${s.from}-${s.to}`}>
          <span className="seg-name">
            {s.from} <i>→</i> {s.to}
          </span>
          <span className="seg-bar">
            <i style={{ width: `${Math.max(s.p * 100, 1.5)}%` }} />
          </span>
          <span className="seg-p">{(s.p * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

/** Delay posterior as a 5–95 interval with the median marked. */
export function DelayGauge({ detail }: { detail: TrainDetail }) {
  // Scale to the interval itself, not to some fixed range: a train that is
  // 20 minutes late with 20 seconds of uncertainty should still show a
  // readable band rather than a sliver pinned to the right edge.
  const { p05, p50, p95 } = detail.delay
  const margin = Math.max((p95 - p05) * 0.35, 45)
  const lo = p05 - margin
  const hi = p95 + margin
  const at = (v: number) => ((v - lo) / (hi - lo)) * 100
  const zeroInRange = lo < 0 && 0 < hi
  return (
    <figure className="chart gauge">
      <figcaption>지연 분포 <span className="dim">5 – 95 백분위</span></figcaption>
      <div className="gauge-track">
        {zeroInRange && <i className="gauge-zero" style={{ left: `${at(0)}%` }} />}
        <i
          className="gauge-span"
          style={{ left: `${at(p05)}%`, width: `${at(p95) - at(p05)}%` }}
        />
        <i className="gauge-median" style={{ left: `${at(p50)}%` }} />
      </div>
      <div className="chart-axis">
        <span>{(detail.delay.p05 / 60).toFixed(1)}분</span>
        <span className="mid">중앙값 {(detail.delay.p50 / 60).toFixed(1)}분</span>
        <span>{(detail.delay.p95 / 60).toFixed(1)}분</span>
      </div>
    </figure>
  )
}
