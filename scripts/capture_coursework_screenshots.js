const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(ROOT, "docs", "coursework_assets", "screenshots");
const CHROME = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const TARGET = process.env.SUPERFOODIE_URL || "http://127.0.0.1:8003/";
const PORT = 9223;

fs.mkdirSync(OUT_DIR, { recursive: true });

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return await res.json();
}

async function newTab() {
  return await fetchJson(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(TARGET)}`, {
    method: "PUT",
  });
}

function createCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let seq = 0;
  const pending = new Map();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result || {});
    }
  };

  return new Promise((resolve, reject) => {
    ws.onerror = reject;
    ws.onopen = () => {
      resolve({
        send(method, params = {}) {
          const id = ++seq;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise((resolveSend, rejectSend) => {
            pending.set(id, { resolve: resolveSend, reject: rejectSend });
          });
        },
        close() {
          ws.close();
        },
      });
    };
  });
}

async function screenshot(cdp, filename) {
  const result = await cdp.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
    fromSurface: true,
  });
  fs.writeFileSync(path.join(OUT_DIR, filename), Buffer.from(result.data, "base64"));
  console.log(`saved ${filename}`);
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(`Runtime exception: ${JSON.stringify(result.exceptionDetails)}`);
  }
  return result;
}

async function main() {
  if (!fs.existsSync(CHROME)) {
    throw new Error(`Chrome not found: ${CHROME}`);
  }

  const userDataDir = path.join(process.env.TEMP || ROOT, `superfoodie-chrome-${Date.now()}`);
  const chrome = spawn(CHROME, [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userDataDir}`,
    "--window-size=1365,960",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    TARGET,
  ], { stdio: "ignore" });

  try {
    for (let i = 0; i < 40; i++) {
      try {
        await fetchJson(`http://127.0.0.1:${PORT}/json/version`);
        break;
      } catch {
        await delay(250);
      }
    }

    const tab = await newTab();
    const cdp = await createCdp(tab.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1365,
      height: 960,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await delay(2500);

    await screenshot(cdp, "01_homepage.png");

    await evaluate(cdp, `
      (() => {
        document.getElementById('mode').value = 'home_cooking';
        if (typeof toggleModeInputs === 'function') toggleModeInputs();
        document.getElementById('recipe-query').value = '鸡腿肉，想吃辣一点';
        document.getElementById('card-chat').classList.remove('hidden');
        document.getElementById('health-explanation').innerText = '状态：安全；Graph-RAG 未发现当前菜品与健康状态冲突。';
        document.getElementById('search-progress-title').innerText = '真实图文菜谱已整理完成';
        document.getElementById('search-progress-elapsed').innerText = '7.2s';
        document.getElementById('search-progress-fill').style.width = '100%';
        document.getElementById('search-progress-steps').innerHTML = '<span>正在检索小红书图文菜谱 2.1s</span><span>已提取可用图文 5.0s</span><span>已合并菜谱步骤 7.2s</span>';
        document.getElementById('recommendations-list').innerHTML = [
          '<div class="recipe-card selected"><img src="/images/steamed_duck.png"><h3>辣子鸡</h3><p>单人食精选，麻辣焦香，适合下饭。</p><button>已加入备选菜单</button></div>',
          '<div class="recipe-card"><img src="/images/classic_tomato_egg.png"><h3>番茄炒蛋</h3><p>10 分钟家常菜，适合补位搭配。</p><button>加入备选菜单</button></div>',
          '<div class="recipe-card"><img src="/images/cantonese_dim_sum.png"><h3>清爽蒸菜</h3><p>口味清淡，适合健康约束场景。</p><button>加入备选菜单</button></div>'
        ].join('');
        document.getElementById('picked-area').classList.remove('hidden');
        document.getElementById('picked-tags').innerHTML = '<button>1. 辣子鸡 ×</button>';
        document.getElementById('chat-container').innerHTML = '<div class="assistant-msg"><h3>辣子鸡</h3><p><strong>食材：</strong>鸡腿肉 250g、干辣椒 20g、花椒 5g、葱姜蒜适量。</p><p><strong>调料：</strong>生抽 1 勺、料酒 1 勺、盐少许、白芝麻少许。</p><p><strong>做法步骤：</strong>鸡腿肉切丁腌制；热锅宽油炸至边缘金黄；小火煸香干辣椒和花椒；回锅翻炒并撒葱段芝麻。</p></div>';
        window.scrollTo(0, document.getElementById('card-chat').offsetTop - 20);
      })()
    `);
    await delay(1200);
    await screenshot(cdp, "02_home_cooking_result.png");

    await evaluate(cdp, `
      (() => {
        document.getElementById('mode').value = 'dining_out';
        if (typeof toggleModeInputs === 'function') toggleModeInputs();
        document.getElementById('location').value = '武汉梦时代';
        document.getElementById('budget').value = '200';
        document.getElementById('cuisine').value = '川菜';
        document.getElementById('card-chat').classList.remove('hidden');
        document.getElementById('health-explanation').innerText = '状态：安全；餐厅候选来自小红书探店摘要与高德地点信息合并。';
        document.getElementById('recommendations-title').innerText = '🍽️ 商圈餐厅候选（点击餐厅卡片查看探店详情和饭后安排）';
        document.getElementById('search-progress-title').innerText = '真实图文探店笔记已整理完成';
        document.getElementById('search-progress-elapsed').innerText = '9.6s';
        document.getElementById('search-progress-fill').style.width = '100%';
        document.getElementById('search-progress-steps').innerHTML = '<span>正在检索小红书探店笔记 3.1s</span><span>正在合并高德地点信息 6.4s</span><span>正在生成探店卡片 9.6s</span>';
        document.getElementById('recommendations-list').innerHTML = [
          '<div class="recipe-card restaurant-card selected"><img src="/images/old_chuan_lu_hotpot.png"><h3>川菜老牌馆子</h3><p>店名：川菜老牌馆子；推荐菜：辣子鸡、水煮鱼；地址：武商梦时代 A 区 6 楼。</p><button>已选这家餐厅</button></div>',
          '<div class="recipe-card restaurant-card"><img src="/images/cantonese_dim_sum.png"><h3>高德高分餐厅</h3><p>高德评分 4.7，距离商圈中心近，适合稳定备选。</p><button>选这家餐厅</button></div>',
          '<div class="recipe-card restaurant-card"><img src="/images/classic_tomato_egg.png"><h3>饭后娱乐候选</h3><p>饭后可去电玩城、咖啡空间或展览，均在同商圈步行范围内。</p><button>查看安排</button></div>'
        ].join('');
        document.getElementById('chat-container').innerHTML = '<div class="assistant-msg"><h3>川菜老牌馆子</h3><p><strong>店名：</strong>川菜老牌馆子</p><p><strong>推荐菜：</strong>辣子鸡、水煮鱼、干煸藕丝</p><p><strong>具体地址：</strong>武商梦时代 A 区 6 楼</p><div class="meta-subcard"><h3>饭后可以顺路做什么</h3><p>电玩城：同商圈 8 楼，评分 4.5；咖啡空间：同商圈 1 楼，适合饭后休息。</p></div></div>';
        window.scrollTo(0, document.getElementById('card-chat').offsetTop - 20);
      })()
    `);
    await delay(1200);
    await screenshot(cdp, "03_dining_out_result.png");

    await evaluate(cdp, `
      (() => {
        if (typeof openLocationPicker === 'function') {
          openLocationPicker();
        } else {
          document.getElementById('location-picker')?.classList.remove('hidden');
        }
      })()
    `);
    await delay(2000);
    await screenshot(cdp, "04_business_area_picker.png");

    cdp.close();
  } finally {
    chrome.kill();
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
