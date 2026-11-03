/* 用本机 Chrome 无头验证大屏渲染：收集控制台错误 + 截图 */
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.argv[2] || 'http://localhost:8000/'
const OUT = process.argv[3] || '../.tmp/verify.png'
const WAIT = Number(process.argv[4] || 15000)

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--enable-unsafe-swiftshader',
      '--use-gl=angle',
      '--use-angle=swiftshader',
      '--window-size=1920,1080',
    ],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1920, height: 1080 })
  const logs = []
  page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`))
  page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))
  page.on('requestfailed', (r) => logs.push(`[reqfail] ${r.url().slice(0, 110)} :: ${r.failure()?.errorText}`))

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await new Promise((r) => setTimeout(r, WAIT))
  await page.screenshot({ path: OUT })

  // 汇报关键运行时状态
  const state = await page.evaluate(() => {
    const canvas = document.querySelector('canvas.maplibregl-canvas')
    return {
      hasCanvas: !!canvas,
      canvasSize: canvas ? [canvas.width, canvas.height] : null,
      bodyText: document.body.innerText.slice(0, 200),
    }
  })
  console.log('STATE:', JSON.stringify(state))
  console.log('LOGS:', logs.length)
  logs.slice(0, 40).forEach((l) => console.log(' ', l.slice(0, 200)))
  await browser.close()
})().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})
