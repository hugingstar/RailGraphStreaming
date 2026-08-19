import { useEffect, useRef } from 'react'
import maplibregl, { type GeoJSONSource, type StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import type { Feed } from '../lib/feed'
import type { Lang, NetworkPayload, PlanPayload, Train } from '../lib/types'

interface Props {
  feed: Feed
  network: NetworkPayload | null
  plan: PlanPayload | null
  selected: string | null
  visible: (t: Train) => boolean
  typeColors: Record<string, string>
  basemap: boolean
  lang: Lang
  onSelect: (id: string | null) => void
}

// Stations worth naming at national zoom, with the name in each supported
// language.  The basemap tiles carry no text of their own (see
// BASEMAP_VARIANT), so this table is the only place map labels come from --
// which is what lets the language switch and the font size actually work.
const MAJOR_STATIONS: Record<string, { en: string; ja: string; zh: string }> = {
  '서울': { en: 'Seoul', ja: 'ソウル', zh: '首尔' },
  '수서': { en: 'Suseo', ja: 'スソ', zh: '水西' },
  '청량리': { en: 'Cheongnyangni', ja: 'チョンニャンニ', zh: '清凉里' },
  '광명': { en: 'Gwangmyeong', ja: 'クァンミョン', zh: '光明' },
  '천안아산': { en: 'Cheonan-Asan', ja: 'チョナン・アサン', zh: '天安牙山' },
  '오송': { en: 'Osong', ja: 'オソン', zh: '五松' },
  '대전': { en: 'Daejeon', ja: 'テジョン', zh: '大田' },
  '동대구': { en: 'Dongdaegu', ja: 'トンデグ', zh: '东大邱' },
  '부산': { en: 'Busan', ja: 'プサン', zh: '釜山' },
  '광주송정': { en: 'Gwangju-Songjeong', ja: 'クァンジュ・ソンジョン', zh: '光州松汀' },
  '목포': { en: 'Mokpo', ja: 'モクポ', zh: '木浦' },
  '익산': { en: 'Iksan', ja: 'イクサン', zh: '益山' },
  '전주': { en: 'Jeonju', ja: 'チョンジュ', zh: '全州' },
  '여수엑스포': { en: 'Yeosu-EXPO', ja: 'ヨス・エキスポ', zh: '丽水世博会' },
  '순천': { en: 'Suncheon', ja: 'スンチョン', zh: '顺天' },
  '진주': { en: 'Jinju', ja: 'チンジュ', zh: '晋州' },
  '창원': { en: 'Changwon', ja: 'チャンウォン', zh: '昌原' },
  '포항': { en: 'Pohang', ja: 'ポハン', zh: '浦项' },
  '울산': { en: 'Ulsan', ja: 'ウルサン', zh: '蔚山' },
  '강릉': { en: 'Gangneung', ja: 'カンヌン', zh: '江陵' },
  '춘천': { en: 'Chuncheon', ja: 'チュンチョン', zh: '春川' },
  '안동': { en: 'Andong', ja: 'アンドン', zh: '安东' },
  '제천': { en: 'Jecheon', ja: 'チェチョン', zh: '堤川' },
  '원주': { en: 'Wonju', ja: 'ウォンジュ', zh: '原州' },
  '만종': { en: 'Manjong', ja: 'マンジョン', zh: '万钟' },
  '경주': { en: 'Gyeongju', ja: 'キョンジュ', zh: '庆州' },
  '태화강': { en: 'Taehwagang', ja: 'テファガン', zh: '太和江' },
  '군산': { en: 'Gunsan', ja: 'クンサン', zh: '群山' },
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

// No `glyphs` key at all: MapLibre validates the style and rejects an explicit
// undefined.  We never use symbol layers -- station names are DOM markers -- so
// the map needs no font resources and works with no network at all.
const STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#ffffff' } }],
}

const BASEMAP_SRC = 'carto-light'

// The label-free variant on purpose: CARTO bakes its text into the tile image,
// always in the local language and at a fixed size, so neither the language
// switch nor the larger type could reach it.  We draw every name ourselves.
const BASEMAP_VARIANT = 'light_nolabels'

/** Great-circle initial bearing from p1 to p2, in degrees clockwise from north. */
function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180
  const phi1 = lat1 * rad
  const phi2 = lat2 * rad
  const dLon = (lon2 - lon1) * rad
  const y = Math.sin(dLon) * Math.cos(phi2)
  const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon)
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360
}

/** Delay threshold (seconds) above which a train counts as "delayed" for marker color. */
const DELAY_THRESHOLD_S = 60

/**
 * The SDF arrowhead that shows each train's heading.  Drawn as a solid dart
 * pointing "up" (north); the layer rotates it to the bearing and pushes it
 * ahead of the dot, so the pair reads as "this train, going that way".
 */
function buildArrowImage(): ImageData {
  const size = 64
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#fff'
  ctx.beginPath()
  ctx.moveTo(size / 2, 3)             // tip
  ctx.lineTo(size - 7, size - 10)     // right barb
  ctx.lineTo(size / 2, size * 0.72)   // notch, so it reads as an arrow not a cone
  ctx.lineTo(7, size - 10)            // left barb
  ctx.closePath()
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

export default function MapView(props: Props) {
  const { feed, network, plan, selected, visible, typeColors, basemap, lang, onSelect } = props
  const holder = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const ready = useRef(false)
  const markers = useRef<maplibregl.Marker[]>([])
  const redraw = useRef<() => void>(() => {})
  const framed = useRef(false)
  const headings = useRef(new Map<string, number>())

  // Latest props for use inside imperative map callbacks.
  const live = useRef({ visible, typeColors, selected })
  live.current = { visible, typeColors, selected }

  // -- create the map once -------------------------------------------------
  useEffect(() => {
    if (!holder.current || map.current) return
    const m = new maplibregl.Map({
      container: holder.current,
      style: STYLE,
      center: [127.75, 36.35],
      zoom: 6.35,
      minZoom: 5.2,
      maxZoom: 12,
      attributionControl: false,
      dragRotate: false,
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    m.touchZoomRotate.disableRotation()
    map.current = m

    m.on('load', () => {
      for (const id of ['rail', 'stations', 'cloud', 'trains', 'focus', 'focusStops']) {
        m.addSource(id, { type: 'geojson', data: EMPTY })
      }

      m.addLayer({
        id: 'rail-casing', type: 'line', source: 'rail',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#cec5b2',
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 7, 10, 17],
        },
      })
      m.addLayer({
        id: 'rail-line', type: 'line', source: 'rail',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-opacity': ['case', ['get', 'hsr'], 0.95, 0.75],
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            5, ['case', ['get', 'hsr'], 3.4, 2.2],
            10, ['case', ['get', 'hsr'], 8.5, 5.5],
          ],
        },
      })

      m.addLayer({
        id: 'focus-line', type: 'line', source: 'focus',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#2b2620',
          'line-opacity': 0.55,
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 2.6, 10, 5.4],
        },
      })

      m.addLayer({
        id: 'station-dot', type: 'circle', source: 'stations',
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['zoom'],
            5, ['case', ['>', ['get', 'weight'], 150], 2.6, 1.3],
            10, ['case', ['>', ['get', 'weight'], 150], 6, 3.2],
          ],
          'circle-color': '#6b7690',
          'circle-opacity': 0.8,
          'circle-stroke-width': 0.8,
          'circle-stroke-color': '#ffffff',
        },
      })

      m.addLayer({
        id: 'focus-stop', type: 'circle', source: 'focusStops',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 2.6, 10, 5.5],
          'circle-color': '#1f2937',
          'circle-opacity': 0.92,
          'circle-stroke-width': 1.4,
          'circle-stroke-color': '#ffffff',
        },
      })

      // The probability cloud.  One short line per density bin, drawn end to end
      // along the track and blurred, so the posterior reads as a glowing smear
      // whose brightness is the likelihood of the train being right there.
      m.addLayer({
        id: 'cloud-halo', type: 'line', source: 'cloud',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-opacity': ['*', 0.30, ['get', 'w']],
          'line-blur': 6,
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 13, 10, 34],
        },
      })
      m.addLayer({
        id: 'cloud', type: 'line', source: 'cloud',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-opacity': ['*', 0.85, ['get', 'w']],
          'line-blur': 1.6,
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 4.2, 10, 11],
        },
      })

      m.addLayer({
        id: 'train-dot', type: 'circle', source: 'trains',
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['zoom'],
            5, ['case', ['get', 'sel'], 5.5, 2.9],
            10, ['case', ['get', 'sel'], 11, 6.2],
          ],
          'circle-color': ['get', 'color'],
          'circle-stroke-width': ['case', ['get', 'sel'], 2.6, 1.8],
          'circle-stroke-color': [
            'case', ['get', 'delayed'], '#ef4444', '#22c55e',
          ],
        },
      })

      m.addImage('train-arrow', buildArrowImage(), { sdf: true })
      m.addLayer({
        id: 'train-arrow', type: 'symbol', source: 'trains',
        layout: {
          'icon-image': 'train-arrow',
          'icon-size': [
            'interpolate', ['linear'], ['zoom'],
            5, ['case', ['get', 'sel'], 0.34, 0.23],
            10, ['case', ['get', 'sel'], 0.60, 0.40],
          ],
          'icon-rotate': ['get', 'heading'],
          'icon-rotation-alignment': 'map',
          // Offsets are in icon space and rotate with it, so -y always means
          // "ahead".  This lifts the arrow clear of the dot instead of letting
          // the two shapes merge into one unreadable blob.
          'icon-offset': [0, -54],
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: {
          // Dark against a white map, not the train's own colour: the arrow has
          // to out-contrast the dot it sits beside, and the dot already carries
          // the type colour.
          'icon-color': '#1f2937',
          'icon-halo-color': '#ffffff',
          'icon-halo-width': 1.4,
          'icon-opacity': 0.98,
        },
      })

      const pop = new maplibregl.Popup({
        closeButton: false, closeOnClick: false, offset: 12, className: 'rg-pop',
      })
      m.on('mouseenter', 'train-dot', (e) => {
        m.getCanvas().style.cursor = 'pointer'
        const f = e.features?.[0]
        if (!f) return
        const p = f.properties as Record<string, string>
        pop
          .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
          .setHTML(
            `<b>${p.name}</b><span>${p.origin} → ${p.destination}</span>` +
              `<span>${p.delay}</span><span class="dim">±${p.spread}km (90%)</span>`,
          )
          .addTo(m)
      })
      m.on('mouseleave', 'train-dot', () => {
        m.getCanvas().style.cursor = ''
        pop.remove()
      })
      m.on('click', 'train-dot', (e) => {
        const id = e.features?.[0]?.properties?.train_id
        if (id) onSelect(String(id))
      })
      m.on('click', (e) => {
        const hit = m.queryRenderedFeatures(e.point, { layers: ['train-dot'] })
        if (!hit.length) onSelect(null)
      })

      ready.current = true
      if (import.meta.env.DEV) {
        ;(window as unknown as Record<string, unknown>).__railgraphMap = m
      }
      m.fire('rg-ready')
    })

    return () => {
      m.remove()
      map.current = null
      ready.current = false
    }
    // onSelect is stable for the lifetime of the app.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // -- static network ------------------------------------------------------
  useEffect(() => {
    const m = map.current
    if (!m || !network) return
    const paint = () => {
      const rail = m.getSource('rail') as GeoJSONSource | undefined
      if (!rail) return
      rail.setData({
        type: 'FeatureCollection',
        features: network.lines.map((l) => ({
          type: 'Feature',
          properties: { color: l.color, hsr: l.hsr, name: l.name },
          geometry: { type: 'LineString', coordinates: l.coords },
        })),
      })
      ;(m.getSource('stations') as GeoJSONSource).setData({
        type: 'FeatureCollection',
        features: network.stations.map((s) => ({
          type: 'Feature',
          properties: { name: s.name, weight: s.weight },
          geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
        })),
      })

      if (!framed.current) {
        framed.current = true
        const b = new maplibregl.LngLatBounds()
        for (const ln of network.lines) for (const c of ln.coords) b.extend(c)
        m.fitBounds(b, { padding: 56, duration: 0 })
      }
    }
    if (ready.current) paint()
    else m.once('rg-ready', paint)
  }, [network])

  // -- station name labels, in the reader's language -----------------------
  useEffect(() => {
    const m = map.current
    if (!m || !network) return
    const paint = () => {
      markers.current.forEach((mk) => mk.remove())
      markers.current = network.stations
        .filter((s) => s.name in MAJOR_STATIONS)
        .map((s) => {
          const el = document.createElement('div')
          el.className = 'rg-station-label'
          el.textContent = lang === 'ko' ? s.name : MAJOR_STATIONS[s.name][lang]
          return new maplibregl.Marker({ element: el, anchor: 'left', offset: [9, 0] })
            .setLngLat([s.lon, s.lat])
            .addTo(m)
        })
    }
    if (ready.current) paint()
    else m.once('rg-ready', paint)
  }, [network, lang])

  // -- optional raster basemap --------------------------------------------
  useEffect(() => {
    const m = map.current
    if (!m) return
    const apply = () => {
      const has = !!m.getSource(BASEMAP_SRC)
      if (basemap && !has) {
        m.addSource(BASEMAP_SRC, {
          type: 'raster',
          tiles: [`https://basemaps.cartocdn.com/${BASEMAP_VARIANT}/{z}/{x}/{y}@2x.png`],
          tileSize: 256,
          attribution: '© OpenStreetMap, © CARTO',
        })
        m.addLayer({ id: 'basemap', type: 'raster', source: BASEMAP_SRC,
          paint: { 'raster-opacity': 0.85 } }, 'rail-casing')
      } else if (!basemap && has) {
        if (m.getLayer('basemap')) m.removeLayer('basemap')
        m.removeSource(BASEMAP_SRC)
      }
    }
    if (ready.current) apply()
    else m.once('rg-ready', apply)
  }, [basemap])

  // -- selected train's route ---------------------------------------------
  useEffect(() => {
    const m = map.current
    if (!m) return
    const apply = () => {
      const focus = m.getSource('focus') as GeoJSONSource | undefined
      const stops = m.getSource('focusStops') as GeoJSONSource | undefined
      if (!focus || !stops) return
      if (!plan) {
        focus.setData(EMPTY)
        stops.setData(EMPTY)
        return
      }
      focus.setData({
        type: 'Feature',
        properties: {},
        geometry: { type: 'LineString', coordinates: plan.coords },
      })
      stops.setData({
        type: 'FeatureCollection',
        features: plan.stop_idx.map((i) => ({
          type: 'Feature',
          properties: { name: plan.nodes[i] },
          geometry: { type: 'Point', coordinates: plan.coords[i] },
        })),
      })
    }
    if (ready.current) apply()
    else m.once('rg-ready', apply)
  }, [plan])

  // -- live trains: written straight into MapLibre, never through React ----
  useEffect(() => {
    const m = map.current
    if (!m) return

    const render = () => {
      if (!ready.current) return
      const cloudSrc = m.getSource('cloud') as GeoJSONSource | undefined
      const trainSrc = m.getSource('trains') as GeoJSONSource | undefined
      if (!cloudSrc || !trainSrc) return

      const { visible: keep, typeColors: colors, selected: sel } = live.current
      const cloud: GeoJSON.Feature[] = []
      const dots: GeoJSON.Feature[] = []

      for (const t of feed.snapshot.trains.values()) {
        if (t.status === 'SCHEDULED' || t.status === 'ARRIVED') continue
        if (!keep(t)) continue
        const color = colors[t.type] ?? '#94a3b8'
        const isSel = t.train_id === sel
        for (let i = 0; i < t.band.length - 1; i++) {
          const [lon0, lat0, w0] = t.band[i]
          const [lon1, lat1, w1] = t.band[i + 1]
          const w = (w0 + w1) / 2
          if (w < 0.05) continue
          cloud.push({
            type: 'Feature',
            properties: { w: isSel ? Math.min(1, w * 1.4) : w, color },
            geometry: { type: 'LineString', coordinates: [[lon0, lat0], [lon1, lat1]] },
          })
        }
        // The band is ordered along the route in the direction of travel, so
        // its endpoints give a stable heading even mid-frame; keep the last
        // known heading for trains whose band has collapsed to a point (e.g.
        // stopped at a platform) instead of snapping to north.
        let heading = headings.current.get(t.train_id) ?? 0
        if (t.band.length >= 2) {
          const [lon0, lat0] = t.band[0]
          const [lon1, lat1] = t.band[t.band.length - 1]
          if (Math.abs(lon1 - lon0) > 1e-6 || Math.abs(lat1 - lat0) > 1e-6) {
            heading = bearing(lat0, lon0, lat1, lon1)
            headings.current.set(t.train_id, heading)
          }
        }

        dots.push({
          type: 'Feature',
          properties: {
            train_id: t.train_id,
            name: t.name,
            origin: t.origin,
            destination: t.destination,
            delay:
              t.delay.p50 >= 60
                ? `${Math.round(t.delay.p50 / 60)}분 지연`
                : t.delay.p50 <= -60
                  ? `${Math.round(-t.delay.p50 / 60)}분 빠름`
                  : '정시',
            spread: t.spread_km.toFixed(1),
            color,
            sel: isSel,
            delayed: t.delay.p50 >= DELAY_THRESHOLD_S,
            heading,
          },
          geometry: { type: 'Point', coordinates: [t.lon, t.lat] },
        })
      }

      cloudSrc.setData({ type: 'FeatureCollection', features: cloud })
      trainSrc.setData({ type: 'FeatureCollection', features: dots })
    }

    redraw.current = render
    const unsub = feed.subscribe(render)
    if (ready.current) render()
    else m.once('rg-ready', render)
    return unsub
  }, [feed])

  // Re-render immediately when filters or selection change, without waiting
  // for the next server tick.
  useEffect(() => {
    if (ready.current) redraw.current()
  }, [visible, selected, typeColors])

  return <div ref={holder} className="map" />
}
