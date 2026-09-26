// ── 作业实验室：prts 作业筛选 + 「跑不了就自动换下一份」 ──
// 独立页面脚本（铁律：新增页面绝不改 app.js，只在 PAGES 表里挂一行）
let _clTimer = null;

async function clApi(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

function renderCopilotLab(container) {
  container.innerHTML = `
  <div style="padding:12px;font-size:12px">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
      <b>作业实验室</b>
      <input id="cl-stage" placeholder="stage_name，例如 act54side_ex02 / main_15-18"
             style="width:280px;padding:4px 6px" />
      <button id="cl-screen">🔍 筛选这一关</button>
      <label><input type="checkbox" id="cl-refresh"> 强制刷新</label>
      <span style="flex:1"></span>
      <span style="color:var(--text3)" id="cl-operbox"></span>
    </div>
    <div id="cl-cands"></div>

    <div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border)">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <b>自动降级跑</b>
        <label>模拟器 #<input id="cl-emu" value="1" style="width:40px;padding:2px 4px"></label>
        <label>最多试 <input id="cl-tries" value="4" style="width:36px;padding:2px 4px"> 份</label>
        <label><input type="checkbox" id="cl-support" checked> 借助战（缺一组可借）</label>
        <label><input type="checkbox" id="cl-potion" checked> 理智不足嗑药</label>
        <button id="cl-run">▶ 跑前 N 份（自动换作业直到三星）</button>
        <button id="cl-stop">■ 停止</button>
      </div>
      <div style="color:var(--text3);margin:6px 0 10px">
        前提：游戏要停在<strong>该关详情页</strong>（右下角有「开始行动」）；编队被拒<strong>不消耗理智</strong>，打输才消耗。
      </div>
      <div id="cl-status"></div>
    </div>
  </div>`;

  document.getElementById('cl-screen').onclick = async () => {
    const stage = document.getElementById('cl-stage').value.trim();
    if (!stage) return alert('先填 stage_name');
    const box = document.getElementById('cl-cands');
    box.innerHTML = '<div style="color:var(--text3)">筛选中…（要访问作业站，约 10~60 秒）</div>';
    try {
      const d = await clApi(`/api/copilot/screen?stage=${encodeURIComponent(stage)}&refresh=` +
                            (document.getElementById('cl-refresh').checked ? 1 : 0));
      document.getElementById('cl-operbox').textContent = '干员箱: ' + (d._operbox || '?');
      clRenderCands(d[stage] || []);
    } catch (e) {
      box.innerHTML = `<div style="color:#e66">${e.message}</div>`;
    }
  };

  document.getElementById('cl-run').onclick = async () => {
    const stage = document.getElementById('cl-stage').value.trim();
    if (!stage) return alert('先填 stage_name');
    try {
      const r = await clApi('/api/copilot/fallback', {
        stage, emu: document.getElementById('cl-emu').value,
        max_tries: parseInt(document.getElementById('cl-tries').value || '4', 10),
        support: document.getElementById('cl-support').checked,
        potion: document.getElementById('cl-potion').checked,
      });
      document.getElementById('cl-status').innerHTML =
        `<div style="color:var(--text3)">已启动 pid=${r.pid}｜日志 ${r.log}</div>`;
      clPoll();
    } catch (e) {
      alert(e.message);
    }
  };
  document.getElementById('cl-stop').onclick = async () => {
    await clApi('/api/copilot/fallback/stop', {});
    clPoll();
  };

  if (_clTimer) clearInterval(_clTimer);
  _clTimer = setInterval(clPoll, 3000);
  clPoll();
}

function clRenderCands(rows) {
  const box = document.getElementById('cl-cands');
  if (!rows.length) { box.innerHTML = '<div style="color:var(--text3)">没有候选（换个 stage_name 试试）</div>'; return; }
  let h = `<table class="tbl" style="width:100%"><thead><tr>
    <th>#</th><th>作业号</th><th>★</th><th>赞</th><th>缺口</th><th>专精/模组要求</th><th>标题</th><th>动作</th>
  </tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const miss = (r.missing || []).length
      ? `<span style="color:#e66">${r.missing.join(', ')}</span>`
      : '<span style="color:#3a3">干员齐</span>';
    const req = (r.requirements || []).length
      ? `<span style="color:#da3">${r.requirements.slice(0, 3).join('; ')}</span>`
      : '<span style="color:#3a3">无</span>';
    const clean = r.clean ? ' ★干净' : '';
    h += `<tr>
      <td>${i + 1}</td>
      <td><code>prts://${r.id}</code></td>
      <td>${r.rating ?? ''}</td><td>${r.like ?? ''}</td>
      <td>${miss}</td><td>${req}</td>
      <td title="${(r.title || '').replace(/"/g, '')}">${(r.title || '').slice(0, 30)}${clean}</td>
      <td><a href="#" onclick="clUse(${r.id});return false">填进表单</a></td>
    </tr>`;
  });
  box.innerHTML = h + '</tbody></table>';
}

function clUse(jid) {
  const s = document.getElementById('cl-stage');
  if (s && !s.value) s.value = '';
  navigator.clipboard?.writeText('prts://' + jid);
  document.getElementById('cl-status').innerHTML =
    `<div style="color:var(--text3)">已复制 <code>prts://${jid}</code>，可直接粘进 MAA 的「作业路径/神秘代码」框</div>`;
}

async function clPoll() {
  const box = document.getElementById('cl-status');
  if (!box) { if (_clTimer) clearInterval(_clTimer); return; }
  try {
    const s = await clApi('/api/copilot/fallback/status');
    let h = `<div style="margin-bottom:6px">状态：` +
      (s.running ? `<b style="color:#3a3">运行中</b>（${s.elapsed}s）` : '<b>空闲</b>') + '</div>';
    if (s.report && s.report.tries) {
      h += `<table class="tbl" style="width:100%"><thead><tr><th>顺序</th><th>作业</th><th>判定</th><th>三星</th><th>证据</th></tr></thead><tbody>`;
      s.report.tries.forEach((t, i) => {
        const v = { unusable: '<span style="color:#e66">编队被拒</span>', failed: '<span style="color:#e66">打输/未三星</span>',
                    done: '<span style="color:#3a3">打完</span>', unknown: '未识别' }[t.verdict] || t.verdict;
        h += `<tr><td>${i + 1}</td><td><code>prts://${t.id}</code></td><td>${v}</td>
              <td>${t.three_star === true ? '✓' : t.three_star === false ? '✗' : ''}</td>
              <td style="font-size:10px;color:var(--text3)">${(t.evidence || '').slice(0, 90)}</td></tr>`;
      });
      h += `</tbody></table><div style="margin-top:6px">结果：<b>${s.report.result || ''}</b></div>`;
    }
    if (s.tail) h += `<pre style="max-height:240px;overflow:auto;font-size:10px;margin-top:8px">${s.tail.replace(/</g, '&lt;')}</pre>`;
    box.innerHTML = h;
  } catch (e) { /* 页面切走时不打扰 */ }
}
