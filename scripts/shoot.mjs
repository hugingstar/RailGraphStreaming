/**
 * Screenshot the running UI with the system Chrome.
 *
 *   node scripts/shoot.mjs <url> <outfile> [waitMs] [clickTrain]
 *
 * Used to verify the map renders, since a headless run actually composites
 * frames (requestAnimationFrame fires), unlike a hidden tab.
 */
import puppeteer from 'puppeteer-core'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const [url = 'http://127.0.0.1:5273/', out = 'shot.png', waitMs = '9000', click = ''] =
  process.argv.slice(2)

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader',
         '--window-size=1600,950'],
  defaultViewport: { width: 1600, height: 950, deviceScaleFactor: 2 },
})

const page = await browser.newPage()
const problems = []
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') problems.push(`${m.type()}: ${m.text()}`)
})
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))

await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise((r) => setTimeout(r, Number(waitMs)))

if (click) {
  await page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (el) el.click()
  }, click)
  await new Promise((r) => setTimeout(r, 3500))
}

const probe = await page.evaluate(() => ({
  conn: document.querySelector('.conn')?.textContent?.trim(),
  clock: document.querySelector('.clock-time')?.textContent,
  kpis: [...document.querySelectorAll('.kpi')].map((k) => k.textContent),
  rows: document.querySelectorAll('.train-list .train').length,
  stationLabels: document.querySelectorAll('.rg-station-label').length,
  canvas: (() => {
    const c = document.querySelector('.map canvas')
    return c ? `${c.width}x${c.height}` : null
  })(),
  detailOpen: !!document.querySelector('.detail'),
  detailTitle: document.querySelector('.detail h2')?.textContent ?? null,
  segments: document.querySelectorAll('.seg').length,
  stops: document.querySelectorAll('.timetable li').length,
}))

await page.screenshot({ path: out })
console.log(JSON.stringify({ probe, problems: problems.slice(0, 12) }, null, 1))
await browser.close()
