// Browser regression checks. Every request is intercepted; no robot is contacted.
// Run with Node + playwright on NODE_PATH. Optional argv[2]: screenshot directory.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let offline = false, pending, stopCount = 0, posts = 0;
    const arm = { configured: true, enabled: true, port: '/dev/test-only', port_present: true,
      armed: true, groups_enabled: false, channels: [1,11,7,8], commanded: {1:1500},
      limits: {low:1300, high:1700, centre:1500, step:40} };
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const json = body => route.fulfill({ json: body });
      if (url.pathname === '/console') return route.fulfill({ contentType: 'text/html',
        body: fs.readFileSync(path.join(__dirname, '../client/robot-console.html'), 'utf8') });
      if (request.method() === 'POST') {
        posts++;
        const body = request.postDataJSON();
        if (body.action === 'stop') { stopCount++; return json({ok:true, stop_write_ok:false, error:'test write failure', note:'ล็อกแล้ว แต่ยังไม่ยืนยันการหยุด'}); }
        pending = () => json({ok:true, at:1540});
        return;
      }
      if (url.pathname === '/arm/state') return offline ? route.abort() : json({ok:true,state:arm});
      if (url.pathname === '/robot/state') return offline ? route.abort() : json({ok:true,state:{
        battery:78, docking:'off_dock', pose:{x:1.25,y:2.4,yaw:0.1}, motion_enabled:false,
        model:'TEST DATA', localization_quality:91, places:['<img src=x onerror=alert(1)>'] }});
      if (url.pathname === '/hardware/reports') return json({reports:[{at:new Date().toISOString()}]});
      throw new Error('Unexpected request ' + url.pathname);
    });
    await page.goto('http://console.test/console?token=test-only');
    await page.locator('[data-up="1"]:enabled').waitFor();
    assert.equal(posts, 0, 'opening page must not write');
    assert.equal(await page.locator('#cPlaces img').count(), 0, 'place names are text');
    assert.match(await page.locator('#nCam').getAttribute('class'), /unknown/);
    await page.locator('#previewActivity').click();
    await page.locator('#activityArm[data-stage="pending"]').waitFor();
    assert.equal(posts, 0, 'preview must not write');
    await page.locator('#activityArm[data-stage="idle"]').waitFor();
    await page.locator('[data-up="1"]').click();
    await page.locator('#activityArm[data-stage="pending"]').waitFor();
    assert.equal(await page.locator('[data-up="1"]').isDisabled(), true);
    await page.locator('#stopArm').click();
    await page.locator('#activityArm[data-stage="failed"]').waitFor();
    assert.equal(stopCount, 1, 'stop bypasses a pending movement');
    assert.match(await page.locator('#activityArmDetail').textContent(), /test write failure/);
    await pending();
    await page.locator('[data-up="1"]:enabled').waitFor();
    assert.equal(await page.locator('#activityArm').getAttribute('data-stage'), 'failed', 'late response must not hide stop failure');
    assert.equal(posts, 2);
    if (process.argv[2]) {
      fs.mkdirSync(process.argv[2], {recursive:true});
      await page.evaluate(() => { document.querySelector('.masthead p').textContent = 'ภาพตรวจหน้าเว็บ · ข้อมูลจำลองสำหรับทดสอบ ไม่ใช่ค่าจากหุ่น'; });
      await page.screenshot({path:path.join(process.argv[2], 'console-desktop.png'), fullPage:true});
    }
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no mobile horizontal overflow');
    if (process.argv[2]) await page.screenshot({path:path.join(process.argv[2], 'console-mobile.png'), fullPage:true});
    offline = true;
    await page.waitForFunction(() => document.getElementById('aChannels').textContent.includes('ปิดการสั่งช่อง'));
    assert.equal(await page.locator('#aRun').isDisabled(), true);
    assert.equal(await page.locator('#cBatt').textContent(), '—', 'old readings cleared on failure');
    assert.equal(await page.locator('#stopArm').isDisabled(), false);
    assert.deepEqual(errors, []);
    console.log('PASS: no startup writes, preview isolation, pending controls, stop priority/failure, late response, stale readings, report honesty, text escaping, mobile layout, no JS errors. All network mocked.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
