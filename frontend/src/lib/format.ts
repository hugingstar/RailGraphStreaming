const KST = 'Asia/Seoul'

export const hhmm = (epochSeconds: number) =>
  new Date(epochSeconds * 1000).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: KST,
  })

export const hhmmss = (epochSeconds: number) =>
  new Date(epochSeconds * 1000).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: KST,
  })

/** Signed delay, in the units a passenger thinks in. */
export function delayLabel(seconds: number): string {
  const s = Math.round(seconds)
  if (Math.abs(s) < 60) return s <= 0 ? '정시' : `${s}초 지연`
  const m = Math.round(Math.abs(s) / 60)
  return s < 0 ? `${m}분 빠름` : `${m}분 지연`
}

export function delayShort(seconds: number): string {
  const m = seconds / 60
  if (Math.abs(m) < 0.5) return '0'
  return `${m > 0 ? '+' : ''}${m.toFixed(m > -10 && m < 10 ? 1 : 0)}`
}

export type DelayTone = 'early' | 'ontime' | 'minor' | 'major'

export function delayTone(seconds: number): DelayTone {
  if (seconds < -30) return 'early'
  if (seconds < 120) return 'ontime'
  if (seconds < 300) return 'minor'
  return 'major'
}

export const km = (v: number) => `${v.toFixed(1)}km`

export const pct = (v: number) => `${(v * 100).toFixed(0)}%`
