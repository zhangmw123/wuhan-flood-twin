import { chromium } from 'playwright-core'

const EXE = 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe'
const BASE = 'http://127.0.0.1:8000'
const OUT = 'C:/tmp/recdemo/out'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await chromium.launch({
  executablePath: EXE,
  headless: true,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--force-device-scale-factor=1', '--hide-scrollbars'],
})
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } },
})
const p = await ctx.newPage()

// ---------- 页面侧演示模块：虚拟鼠标 / 字幕 / 卡片 ----------
await p.addInitScript(() => {
  const ready = (fn) => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn()
  ready(() => {
    if (window.__demo) return
    const style = document.createElement('style')
    style.textContent = `
      #demo-cursor{position:fixed;z-index:99998;left:0;top:0;pointer-events:none;filter:drop-shadow(0 2px 6px rgba(0,0,0,.6));transition:none}
      #demo-ripple{position:fixed;z-index:99997;pointer-events:none;border:3px solid rgba(0,229,255,.9);border-radius:50%;opacity:0}
      #demo-ripple.go{animation:demoRipple .55s ease-out}
      @keyframes demoRipple{0%{opacity:.95;transform:scale(.3)}100%{opacity:0;transform:scale(1.6)}}
      #demo-caption{position:fixed;z-index:99999;left:50%;bottom:118px;transform:translateX(-50%) translateY(14px);
        max-width:1200px;padding:14px 30px;background:rgba(3,16,26,.88);border:1px solid rgba(0,229,255,.45);
        border-radius:12px;color:#e8fbff;font:600 25px/1.5 "Microsoft YaHei",sans-serif;letter-spacing:1px;
        box-shadow:0 8px 40px rgba(0,0,0,.5),0 0 24px rgba(0,229,255,.12);opacity:0;transition:opacity .4s,transform .4s;pointer-events:none;text-align:center}
      #demo-caption.show{opacity:1;transform:translateX(-50%) translateY(0)}
      #demo-caption b{color:#00e5ff}
      #demo-card{position:fixed;inset:0;z-index:100000;display:flex;flex-direction:column;align-items:center;justify-content:center;
        background:radial-gradient(1200px 700px at 50% 42%,rgba(0,60,90,.55),rgba(1,8,14,.97) 70%),rgba(1,8,14,.97);
        opacity:0;transition:opacity .6s;pointer-events:none;text-align:center;color:#eafaff;font-family:"Microsoft YaHei",sans-serif}
      #demo-card.show{opacity:1}
      #demo-card h1{font-size:56px;margin:0 0 18px;letter-spacing:3px;text-shadow:0 0 30px rgba(0,229,255,.45)}
      #demo-card .sub{font-size:22px;color:#8fd4ea;margin:0 0 34px;letter-spacing:1px}
      #demo-card .foot{font-size:19px;color:#5f93a8;letter-spacing:2px}
      #demo-card ul{list-style:none;margin:0;padding:0;font-size:24px;line-height:2.2;color:#d9f6ff}
      #demo-card ul b{color:#00e5ff;font-weight:700}
      #demo-card .badge{margin-top:34px;font-size:18px;color:#5f93a8;letter-spacing:2px}
    `
    document.head.appendChild(style)

    const cur = document.createElement('div')
    cur.id = 'demo-cursor'
    cur.innerHTML = `<svg width="30" height="34" viewBox="0 0 30 34"><path d="M4 2 L4 27 L11.5 20.5 L16.5 31 L21.5 28.5 L16.5 18.5 L26 18 Z" fill="#eafaff" stroke="#0a2230" stroke-width="1.6"/></svg>`
    cur.style.transform = 'translate(960px,540px)'
    document.body.appendChild(cur)

    const rip = document.createElement('div')
    rip.id = 'demo-ripple'
    document.body.appendChild(rip)

    const cap = document.createElement('div')
    cap.id = 'demo-caption'
    document.body.appendChild(cap)

    const card = document.createElement('div')
    card.id = 'demo-card'
    document.body.appendChild(card)

    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

    window.__demo = {
      async move(x, y, ms = 650) {
        const m = cur.style.transform.match(/translate\(([\d.]+)px,\s*([\d.]+)px\)/)
        const x0 = m ? +m[1] : 960, y0 = m ? +m[2] : 540
        const t0 = performance.now()
        return new Promise((res) => {
          const step = (now) => {
            const t = Math.min(1, (now - t0) / ms)
            const k = ease(t)
            cur.style.transform = `translate(${x0 + (x - x0) * k}px,${y0 + (y - y0) * k}px)`
            t < 1 ? requestAnimationFrame(step) : res()
          }
          requestAnimationFrame(step)
        })
      },
      ripple(x, y) {
        rip.style.left = x - 26 + 'px'; rip.style.top = y - 26 + 'px'
        rip.style.width = rip.style.height = '52px'
        rip.classList.remove('go'); void rip.offsetWidth; rip.classList.add('go')
      },
      say(html, ms = 4200) {
        cap.innerHTML = html
        cap.classList.add('show')
        return sleep(ms).then(() => { cap.classList.remove('show'); return sleep(450) })
      },
      hold() { cap.classList.add('show') },
      drop() { cap.classList.remove('show') },
      async title(o, ms = 5200) {
        card.innerHTML = o.html
        card.classList.add('show')
        await sleep(ms)
        card.classList.remove('show')
        await sleep(700)
      },
    }
  })
})

await p.goto(BASE, { waitUntil: 'load', timeout: 60000 })

// 等待应用就绪：KPI 有值 + 地图实例存在
await p.waitForFunction(() => {
  const kpi = document.querySelector('.kpis .value')
  return kpi && kpi.textContent.trim() && (window).__floodMap
}, { timeout: 60000 })
await p.waitForTimeout(5000)

// ---------- 辅助 ----------
const demo = (name, ...args) => p.evaluate(([n, ...a]) => (window).__demo[n](...a), [name, ...args])
const fly = async (o) => { await p.evaluate((o) => (window).__floodMap.flyTo({ ...o, essential: true }), o); await sleep(o.duration + 600) }

async function clickEl(sel, text) {
  const loc = text ? p.locator(`${sel}`, { hasText: text }).first() : p.locator(sel).first()
  const box = await loc.boundingBox()
  if (!box) throw new Error('not found: ' + sel + ' ' + (text || ''))
  const x = box.x + box.width / 2, y = box.y + box.height / 2
  await demo('move', x, y, 600)
  await demo('ripple', x, y)
  await p.mouse.click(x, y)
  await sleep(500)
  return box
}

// ---------- S0 开场卡片 ----------
await demo('title', { html: `
  <h1>武汉城市内涝二三维孪生调度平台</h1>
  <p class="sub">Jianghan Basin Flood Control & Urban Waterlogging Digital Twin</p>
  <div class="foot">作品演示&nbsp;&nbsp;·&nbsp;&nbsp;Vue3 + MapLibre GL&nbsp;&nbsp;·&nbsp;&nbsp;FastAPI&nbsp;&nbsp;·&nbsp;&nbsp;纯 Python 几何引擎</div>
` }, 5200)

// ---------- S1 全景与数据底座 ----------
await fly({ center: [114.30, 30.585], zoom: 10.4, pitch: 0, bearing: 0, duration: 5000 })
await demo('say', '全市 <b>20.6 万栋建筑 · 2.9 万条道路</b> 全矢量本地渲染，零外部瓦片服务依赖', 5200)
await fly({ center: [114.27, 30.60], zoom: 11.7, pitch: 55, bearing: -25, duration: 6500 })
await demo('say', '<b>13 个行政区</b>边界 · 14 个湖泊真实 OSM 水系 · 长江 / 汉江干流矢量', 5200)
await fly({ center: [114.35, 30.545], zoom: 12.2, pitch: 50, bearing: 15, duration: 6000 })
await demo('say', '沙盘时钟 <b>3 秒 = 10 分钟</b>，24 小时雨情水情连续推演', 4500)

// ---------- S2 实时监测与预警 ----------
await fly({ center: [114.30, 30.585], zoom: 10.8, pitch: 30, bearing: 0, duration: 5000 })
await demo('say', '<b>SSE 实时预警流</b>滚动推送 · 汉口水文站水位-流量过程线 · 全市雨强 TOP10', 5500)

// ---------- S3 内涝推演 ----------
await demo('move', 1700, 1040, 700)
const slider = await p.locator('.rain-slider').first().boundingBox()
await demo('move', slider.x + slider.width * 0.75, slider.y + slider.height / 2, 800)
await demo('ripple', slider.x + slider.width * 0.75, slider.y + slider.height / 2)
await p.mouse.click(slider.x + slider.width * 0.75, slider.y + slider.height / 2)
await sleep(600)
await clickEl('.control-row .group:nth-of-type(2) button', '6h')
await demo('say', '设置情景：<b>累计雨量 200mm · 历时 6h</b>', 3200)
await clickEl('.btn.primary', '开始内涝推演')
await demo('hold'); await demo('say', '<b>产流 — 汇水 — 泵排 — 淹没</b> 四阶段水量平衡模型推演中…', 3000)
await p.waitForSelector('.impact-grid', { timeout: 120000 })
await sleep(1500)
await demo('say', '空间叠加计算：<b>淹没面积 / 受淹建筑 / 阻断道路</b>，逐区统计实时生成', 5500)
await fly({ center: [114.29, 30.585], zoom: 12.6, pitch: 60, bearing: -30, duration: 6000 })

// ---------- S4 应急避险路径规划 ----------
await clickEl('.route-panel .panel-head .btn')
await sleep(1200)
// 点选受灾点后，在地图中心点击
const canvas = await p.locator('canvas.maplibregl-canvas').first().boundingBox()
const cx = canvas.x + canvas.width * 0.5, cy = canvas.y + canvas.height * 0.45
await demo('move', cx, cy, 700)
await demo('ripple', cx, cy)
await p.mouse.click(cx, cy)
await p.waitForSelector('.route-stats', { timeout: 60000 }).catch(() => {})
await sleep(2500)
await demo('say', '<b>A* 路径规划</b>：自动避开积水阻断路段，匹配最近且容量充足的避难所', 5500)

// ---------- S5 会商简报 ----------
await clickEl('.btn.report-btn')
await p.waitForSelector('.report-box', { timeout: 60000 })
await sleep(2000)
await demo('say', '一键生成<b>编号会商简报</b>：支持 Markdown 下载与 A4 打印导出 PDF', 5200)
await p.keyboard.press('Escape')
await sleep(900)

// ---------- S6 AI Copilot ----------
await clickEl('.copilot-btn')
await sleep(900)
const inp = p.locator('.input-row input').first()
await demo('move', 960, 1020, 700)
await inp.click()
await inp.fill('武昌区积水超过 40cm 的点位，飞过去并做 500 米缓冲分析')
await sleep(600)
await clickEl('.input-row .btn', '发送')
await p.waitForTimeout(7000)
await demo('say', 'AI 调度助手：<b>自然语言 → 工具调用</b>，查点 / 飞行 / 缓冲分析自动执行', 5500)
await clickEl('.copilot-btn')
await sleep(800)

// ---------- S7 结尾卡片 ----------
await demo('title', { html: `
  <h1 style="font-size:44px">工程亮点</h1>
  <ul>
    <li><b>纯 Python 几何引擎</b>：点面判定 / 缓冲 / 裁剪 / A*，可平滑切换 PostGIS</li>
    <li><b>性能优化</b>：瓦片 LRU + 空间索引 + orjson，P95 8756ms → 1126ms（8.4×）</li>
    <li><b>质量保障</b>：45 项单元测试不变量 · 17 项链路自检 · 并发压测 0 错误</li>
    <li><b>感知适配</b>：三通道数据接入，实测 / 模型双口径明确标注</li>
  </ul>
  <div class="badge">WUHAN FLOOD DIGITAL TWIN · 2026</div>
` }, 6500)

await sleep(1200)
await ctx.close()
const video = await p.video()
console.log('VIDEO:', video)
await browser.close()
