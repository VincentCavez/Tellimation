// Headless batch recording of the video_ui scenes. Chromium in the background
// (hidden tab) throttles requestAnimationFrame and stalls the batch; headless runs at full rate.
// Setup (outside the repo): mkdir /tmp/rec && cd /tmp/rec && npm i playwright && npx playwright install chromium
// Run (video_ui server on 5557): node scripts/study2/record_videos.js "http://127.0.0.1:5557/?only=I2,C3,S1&prefix=study2_&save=study2_videos"
// with NODE_PATH=/tmp/rec/node_modules so `require("playwright")` resolves.
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2];
  const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') console.log('[page error]', m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => typeof allScenes !== 'undefined' && allScenes.length > 0);
  const ids = await page.evaluate(() => allScenes.map((s) => s.story_id));
  console.log('scenes:', ids.join(' '));
  const t0 = Date.now();
  await page.evaluate(() => startBatch());
  const rows = await page.evaluate(() => [...document.querySelectorAll('.batch-row')].map((r) => r.textContent.trim().replace(/\s+/g, ' ')));
  console.log(rows.join('\n'));
  console.log('elapsed s:', ((Date.now() - t0) / 1000).toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
