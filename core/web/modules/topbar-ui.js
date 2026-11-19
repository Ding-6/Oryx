/* topbar-ui.js —— 分区 21。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { closeModal, openModal } from './modal.js';
import { badge, loadModelData, paintModeHint, refreshState } from './models.js';
import { $, $$, S, api, esc, escAttr, toast } from './core.js';

/* ══════ 顶栏交互 ══════ */

/**
 * 切换交互模式。
 *
 * 抽出来是因为窄屏抽屉也要用 —— 以前抽屉只能"看"不能"改"，
 * 用户在窄屏下切换模式后抽屉里的状态不变，像是没生效。
 */
async function setInteraction(mode) {
  const r = await api('/api/interaction', { method: 'POST', body: JSON.stringify({ interaction: mode }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  S.interaction = mode;
  $$('#interSeg button').forEach(b => b.classList.toggle('on', b.dataset.i === mode));
  paintModeHint();
  await refreshState();
  return true;
}

/** 切换人设。 */
async function setExpert(id) {
  const r = await api('/api/expert', { method: 'POST', body: JSON.stringify({ expert: id }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  await refreshState();
  toast(id ? '已切换人设' : '已清除人设');
  return true;
}

/** 切换回答风格。 */
async function setStyle(name) {
  const r = await api('/api/style', { method: 'POST', body: JSON.stringify({ style: name }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  await refreshState();
  return true;
}

/** 切换计算模式（效率 / 本地）。 */
async function setComputeMode(mode) {
  const r = await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  // 切模式会换掉当前 provider —— 芯片要立刻跟上，
  // 不刷新的话顶栏还显示着上一个模式的模型，看起来像没切。
  await refreshState();
  toast(mode === 'local' ? '已切到本地模式' : '已切到效率模式');
  return true;
}

$$('#interSeg button').forEach(b => b.addEventListener('click', async () => {
  await setInteraction(b.dataset.i);
}));
$('#expertSel').addEventListener('change', async e => {
  await setExpert(e.target.value);
});
$('#styleSel').addEventListener('change', async e => {
  await setStyle(e.target.value);
});
$('#modeSel').addEventListener('change', async e => {
  await setComputeMode(e.target.value);
});

/**
 * 顶栏设置抽屉 —— 窄屏时那些选择器会被隐藏（放不下），
 * 这个抽屉是它们的替代入口。
 *
 * 以前没有它：窄屏（<620px）下模式、风格、人设、模型**全部无法切换**，
 * 而且设置页里也没有这几个的完整入口 —— 用户在这个宽度下就是残废的。
 */
$('#topMoreBtn').addEventListener('click', async () => {
  const s = await api('/api/state').then(r => r.json()).catch(() => ({}));
  const pv = await api('/api/providers').then(r => r.json()).catch(() => ({ items: [], active: '' }));
  const experts = s.experts || [];
  const styles = s.styles || [];
  const models = pv.items || [];
  const curExpert = s.expert || '';
  const curStyle = s.style || 'professional';
  const curMode = s.mode || 'efficiency';

  openModal({
    title: '对话设置',
    body: `<div class="topdrawer">
      <div class="fld"><label>交互模式</label>
        <div class="seg" id="drMode">${[['ask','问答'],['plan','规划'],['craft','执行']]
          .map(([k,n]) => `<button data-v="${k}" class="${(s.interaction||'craft')===k?'on':''}" type="button">${n}</button>`).join('')}</div>
        <div class="hint">问答与规划只给只读工具；执行模式开放全部工具，危险动作仍会弹确认。</div>
      </div>
      <div class="fld"><label for="drExpert">人设 / 场景角色</label>
        <select class="inp" id="drExpert">
          <option value="">无专家</option>
          ${experts.map(x => `<option value="${escAttr(x.id)}" ${curExpert===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}
        </select>
        <div class="hint">${curExpert ? '当前：' + esc((experts.find(x => x.id === curExpert) || {}).name || curExpert) : '未设置人设'}</div>
      </div>
      <div class="fld"><label for="drStyle">回答风格</label>
        <select class="inp" id="drStyle">
          ${styles.map(x => `<option value="${escAttr(x.name)}" ${curStyle===x.name?'selected':''}>${esc(x.label || x.name)}</option>`).join('')}
        </select>
      </div>
      <div class="fld"><label for="drModeSel">计算模式</label>
        <select class="inp" id="drModeSel">
          <option value="efficiency" ${curMode==='efficiency'?'selected':''}>效率模式（云端理解 + 本地索引）</option>
          <option value="local" ${curMode==='local'?'selected':''}>本地模式（全本地，可断网）</option>
        </select>
      </div>
      <div class="fld"><label for="drModel">模型服务</label>
        <select class="inp" id="drModel">
          ${models.map(x => `<option value="${escAttr(x.name)}" ${pv.active===x.name?'selected':''}>${esc(x.name)}${x.model?' · '+esc(x.model):''}</option>`).join('')}
        </select>
        <div class="hint">当前：${esc(pv.active || '（未设置）')}</div>
      </div>
    </div>`,
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });

  // 模式切换：立即生效，不用等「保存」
  $$('#drMode button').forEach(b => b.addEventListener('click', async () => {
    const ok = await setInteraction(b.dataset.v);
    if (!ok) return;
    $$('#drMode button').forEach(x => x.classList.toggle('on', x === b));
  }));
  const de = $('#drExpert');
  if (de) de.addEventListener('change', async e => { await setExpert(e.target.value); });
  const ds = $('#drStyle');
  if (ds) ds.addEventListener('change', async e => { await setStyle(e.target.value); });
  const dm = $('#drModeSel');
  if (dm) dm.addEventListener('change', async e => { await setComputeMode(e.target.value); });
  const dmo = $('#drModel');
  if (dmo) dmo.addEventListener('change', async e => {
    const r = await api(`/api/providers/${encodeURIComponent(e.target.value)}/activate`, { method: 'POST' });
    if (!r.ok) {
      const x = await r.json().catch(() => ({}));
      toast('切换失败：' + (x.detail || r.status));
      return;
    }
    await loadModelData();
    await refreshState();
    toast('已切换到 ' + e.target.value);
  });
});
$('#modelChip').addEventListener('click', async () => {
  const [pv, loc] = await Promise.all([
    api('/api/providers').then(r => r.json()),
    api('/api/providers/local').then(r => r.json()).catch(() => ({ models: [] })),
  ]);
  const items = pv.items || [];
  const localModels = loc.models || [];
  // 按计算模式分区：本地模式只列本机模型，效率模式只列云端模型。
  // 两个都列出来的话，用户在「本地模式」下点一个云端模型，
  // 切过去还是本地 provider —— 看起来就是"切换没生效"。
  const localMode = S.mode === 'local';
  const cloudItems = items.filter(it => !it.is_local);
  const localItems = items.filter(it => it.is_local);
  const shown = localMode ? localItems : cloudItems;

  openModal({
    title: localMode ? '切换本地模型' : '切换云端模型',
    body: shown.length ? `
      <div class="picklist">
      ${shown.map(it => `
        <button class="pick ${it.name === pv.active ? 'on' : ''}" data-pv="${esc(it.name)}" type="button">
          <span class="pk">${it.is_local ? '◇' : '◈'}</span>
          <span><span class="pt">${esc(it.name)} ${it.name === pv.active ? '<span class="badge done">使用中</span>' : ''}</span>
            <div class="pd">${esc(it.model || '')} · ${esc(it.type)} · ${Math.round((it.capabilities?.context_window || 0) / 1000)}k 上下文</div></span>
        </button>`).join('')}
      </div>
      ${localMode && localModels.length ? `
        <div class="sect" style="margin-top:18px"><h2>本机已装 · 点一下直接用</h2>
        <div class="picklist">
        ${localModels.map(m => `
          <button class="pick ${m.provider && m.provider === pv.active ? 'on' : ''}" data-lm="${esc(m.name)}" type="button">
            <span class="pk">◇</span>
            <span><span class="pt">${esc(m.name)}${m.provider && m.provider === pv.active ? ' <span class="badge done">使用中</span>' : ''}</span>
              <div class="pd">本地 · ${esc(m.parameter_size || m.family || '')} ${esc(m.quantization || '')} · ${(m.size / 1024 / 1024 / 1024).toFixed(1)} GB</div></span>
          </button>`).join('')}
        </div>
        <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:8px">选一个会自动登记成模型服务并切换过去。</div>
      </div>` : ''}
      <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:12px">
        ${localMode ? '当前是本地模式，只显示跑在本机的模型。' : '当前是效率模式，只显示云端模型。'}
        要换一类，去顶栏的「计算模式」切。
      </div>`
      : `<div class="empty">${localMode ? '还没有本机模型。去设置里添加，或先用 Ollama 装一个。' : '还没有云端模型服务。去设置里添加。'}</div>`,
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });
  $$('#modal [data-pv]').forEach(b => b.addEventListener('click', async () => {
    // 失败时**不要关弹窗** —— 以前不管成败都 closeModal()，
    // 切换失败时用户看到的是「弹窗消失了，什么也没变」，
    // 误以为已经切好了。失败要留在原地并说明原因。
    b.disabled = true;
    try {
      const r = await api(`/api/providers/${encodeURIComponent(b.dataset.pv)}/activate`, { method: 'POST' });
      if (!r.ok) {
        const x = await r.json().catch(() => ({}));
        toast('切换失败：' + (x.detail || r.status));
        b.disabled = false;
        return;
      }
      closeModal();
      await refreshState();
      toast('已切换到 ' + b.dataset.pv);
    } catch (e) {
      toast('切换失败：' + (e && e.message ? e.message : e));
      b.disabled = false;
    }
  }));
  // 本机已装模型：登记成 provider 后直接激活
  $$('#modal [data-lm]').forEach(b => b.addEventListener('click', async () => {
    const model = b.dataset.lm;
    b.disabled = true;
    try {
      // api() 返回的是 Response，要自己解 JSON —— 直接读 r.name 永远是 undefined
      const resp = await api('/api/providers/local', {
        method: 'POST',
        body: JSON.stringify({ model }),
      });
      const r = await resp.json().catch(() => ({}));
      if (!resp.ok || !r.name) {
        toast('登记失败：' + (r.detail || resp.status || '未知原因'));
        b.disabled = false;
        return;
      }
      // 登记成功了，但**激活**可能失败（密钥无效、配置冲突）。
      // 失败时别关弹窗 —— 用户要看到到底哪一步没成。
      const act = await api(`/api/providers/${encodeURIComponent(r.name)}/activate`, { method: 'POST' });
      if (!act.ok) {
        const x = await act.json().catch(() => ({}));
        toast('已登记，但切换失败：' + (x.detail || act.status));
        b.disabled = false;
        await loadModelData();
        await refreshState();
        return;
      }
      closeModal();
      await refreshState();
      toast('已切换到 ' + model);
    } catch (e) {
      toast('切换失败：' + e.message);
      b.disabled = false;
    }
  }));
});
