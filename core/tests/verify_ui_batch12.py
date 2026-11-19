"""第十二批的界面验收：真跑一遍 headless Chrome，断言用户能看到的东西。

为什么不用单测：这一批改的全是「看起来对不对」——
卡片一不高、按钮不在同一个位置、点本地资料会不会把对话栏顶走、
删除有没有确认弹窗。这些都是**界面层语义**，静态检查与接口测试都测不出来。

做法：起一个本地的代理服务（端口 8799）：
  · GET /       → 真页面（注入令牌）+ 验收脚本
  · /api/*      → 转发到真正的后端 8765，并把令牌头补上
这样 Chrome 打开的是**同源**页面，fetch('/api/...') 不用管 CORS，
页面里的 DOM 也是完整的（真 HTML，不是拼出来的壳）。
页面跑完把结果写进 #acc-out，用 --dump-dom 读回来解析。

运行：python -m tests.verify_ui_batch12
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web" / "index.html"
BACKEND = "http://127.0.0.1:8765"
PROBE_PORT = 8799
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("msedge")


# ── 页面里跑的验收脚本 ──────────────────────────────────────────
PROBE = r"""
<script>
(async () => {
  const R = {};
  let step = 'start';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const q = s => document.querySelector(s);
  // 等某个选择器出现（最多 timeout 毫秒）。
  //
  // 为什么不能死等 sleep(1500)：设置页要拉 6 个接口（/api/state、/api/workspace、
  // /api/appearance、/api/capabilities…），后端刚重启时这些接口慢，
  // 固定 1500ms 偶尔不够 —— 表现是「能力分区不存在」，
  // 其实是渲染还没完成（实测撞到过一次）。
  const waitFor = async (sel, timeout = 8000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const el = q(sel);
      if (el) return el;
      await sleep(120);
    }
    return null;
  };
  const post = obj => fetch('/__acc_result', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  }).catch(() => {});
  try {
    R.booted = true;
    await post({ booted: true, at: Date.now() });
    // 先等页面稳定下来再量。
    // 页面刚 load 时字体与布局还没落定，第一次量出来的数值是「没稳定」的值 ——
    // 差个七八像素，看起来像是「点一下浮层把列表顶走了」，其实是量错了时机。
    await sleep(1500);
    /* ── 1. 左栏：导航不滚、只有会话列表能滚 ── */
    step = 'rail';
    const nav = q('.railnav');
    R.navOverflow = getComputedStyle(nav).overflowY;
    R.navScrollable = nav.scrollHeight > nav.clientHeight + 1;
    R.railScrollables = [...document.querySelectorAll('#rail *')]
      .filter(el => {
        const o = getComputedStyle(el);
        return (o.overflowY === 'auto' || o.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1;
      })
      .map(el => el.id || el.className);

    /* ── 2. 点「本地资料」不能把对话列表顶走 ── */
    step = 'local-flyout';
    const groupTop0 = q('.railgroup').getBoundingClientRect().top;
    const sessTop0 = q('#sessList').getBoundingClientRect().top;
    const navH0 = q('.railnav').getBoundingClientRect().height;
    q('.navitem.localhead').click();
    await sleep(200);
    const sub = q('#localSub');
    const subRect = sub.getBoundingClientRect();
    R.subHidden = sub.hidden;
    R.subVisible = subRect.width > 0 && subRect.height > 0;
    R.subPosition = getComputedStyle(sub).position;
    R.groupTop0 = groupTop0;
    R.groupTop1 = q('.railgroup').getBoundingClientRect().top;
    R.sessTop0 = sessTop0;
    R.sessTop1 = q('#sessList').getBoundingClientRect().top;
    R.navH0 = navH0;
    R.navH1 = q('.railnav').getBoundingClientRect().height;
    R.groupUnmoved = Math.abs(R.groupTop1 - groupTop0) < 0.5;
    R.sessUnmoved = Math.abs(R.sessTop1 - sessTop0) < 0.5;
    // 谁把导航顶高了？逐个量一下
    R.railParts = ['.railhead', '.railnav', '.nav', '.navgroup'].map(sel => {
      const el = q(sel);
      return sel + '=' + (el ? Math.round(el.getBoundingClientRect().height) : 'null');
    }).join(' ');
    R.searchBoxExists = !!q('#searchBox');
    R.railcardH = Math.round(q('.railcard').getBoundingClientRect().height);
    R.railfootTop = Math.round(q('.railfoot').getBoundingClientRect().top);
    R.subItems = [...sub.querySelectorAll('.subitem')].map(x => x.textContent.trim());
    setLocalOpen(false);

    /* ── 3. 「新建对话」是独立操作项，不参与位置高亮 ── */
    step = 'newchat';
    const nc = q('.navitem.newchat');
    R.newchatExists = !!nc;
    R.newchatText = nc ? nc.textContent.trim() : '';
    setView('work');
    await sleep(300);
    R.newchatOnAfterNav = nc.classList.contains('on');
    R.workOn = q('.navitem[data-view="work"]').classList.contains('on');
    setView('chat');
    await sleep(200);

    /* ── 4. 设置→能力：卡片统一尺寸 + 按钮同位置 ── */
    step = 'capability';
    setView('settings');
    // 等设置页真的渲染出来（它要拉 6 个接口，后端刚重启时会慢）
    const capSec = await waitFor('#page [data-sec="capability"]');
    R.capSecFound = !!capSec;
    if (capSec) {
      try { capSec.click(); } catch (e) { R.capClickErr = String(e && e.message || e); }
    }
    await waitFor('#capGrid');
    R.capGridExists = !!q('#capGrid');
    R.capMainLen = q('#setMain') ? q('#setMain').innerHTML.length : -1;
    R.capMainHead = q('#setMain') ? q('#setMain').innerHTML.slice(0, 200) : '';
    const cards = [...document.querySelectorAll('#capGrid .uc')];
    R.capCount = cards.length;
    R.capHeights = [...new Set(cards.map(c => Math.round(c.getBoundingClientRect().height)))];
    // 同一行内，描述与按钮的顶部应该一致
    const rowKey = c => Math.round(c.getBoundingClientRect().top / 10);
    const byRow = {};
    cards.forEach(c => { (byRow[rowKey(c)] = byRow[rowKey(c)] || []).push(c); });
    R.capRowAlign = Object.values(byRow).every(row => {
      const d = new Set(row.map(c => Math.round(c.querySelector('.udesc').getBoundingClientRect().top)));
      const f = new Set(row.map(c => Math.round(c.querySelector('.ufoot').getBoundingClientRect().top)));
      return d.size === 1 && f.size === 1;
    });
    R.capClamp = getComputedStyle(cards[0].querySelector('.udesc')).webkitLineClamp;
    R.capPrimaryLabel = cards[0].querySelector('.ufoot .btn.primary').textContent.trim();
    R.capAllHavePrimary = cards.every(c => !!c.querySelector('.ufoot .btn.primary'));
    R.capAllSameLabel = cards.every(c => c.querySelector('.ufoot .btn.primary').textContent.trim() === '在对话中使用');

    /* ── 5. 点卡片描述 → 详情弹窗 ── */
    step = 'cap-detail';
    cards[0].querySelector('.udesc').click();
    await sleep(300);
    const m = q('#modal');
    R.capDetailOpen = q('#mask').classList.contains('on');
    R.capDetailRows = [...m.querySelectorAll('.ucdetail .urow')].map(r => r.querySelector('.ul').textContent.trim());
    R.capDetailHasUse = !!m.querySelector('#capUseNow');
    closeModal();
    await sleep(150);

    /* ── 6. 技能广场：技能卡也统一 ── */
    step = 'market';
    setView('market');
    await waitFor('#mkBody');
    const skCards = [...document.querySelectorAll('#mkBody .uc')];
    R.skillCount = skCards.length;
    R.skillHeights = [...new Set(skCards.map(c => Math.round(c.getBoundingClientRect().height)))];
    R.skillPrimaryLabel = skCards.length ? skCards[0].querySelector('.ufoot .btn.primary').textContent.trim() : '';
    if (skCards.length) {
      skCards[0].querySelector('.uname').click();
      await sleep(600);
      const m2 = q('#modal');
      R.skillDetailOpen = q('#mask').classList.contains('on');
      R.skillDetailHasEdit = !!m2.querySelector('#skEditNow');
      R.skillDetailHasDel = !!m2.querySelector('#skDelNow');
      m2.querySelector('#skDelNow').click();
      await sleep(300);
      const m3 = q('#modal');
      R.skillConfirmOpen = q('#mask').classList.contains('on');
      R.skillConfirmTitle = m3.querySelector('.mhead h3').textContent;
      R.skillConfirmHasCancel = !!m3.querySelector('.mfoot [data-close]');
      closeModal();
      await sleep(150);
    }

    /* ── 7. 自动任务：编辑 + 删除确认 ── */
    step = 'automations';
    // 自己造一条 —— 界面上有没有已建任务取决于用户环境，
    // 验收不能依赖「恰好有」。
    await fetch('/api/automations', {
      method: 'POST',
      headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '__acc_auto', cron: '0 9 * * 1-5', goal: '验收用' }),
    });
    setView('automations');
    // 等**自己造的那条**出现 —— 只等「页面有卡」是不够的：
    // 页面上原本就有卡（用户环境里的自动任务），等到了就返回，
    // 但新创建的那条还没渲染出来，后面 find 会找不到。
    // 用 JS 找而不是 CSS 选择器：:has-text() 不是标准选择器，
    // querySelector 会直接抛错。
    const autoCard = await (async () => {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        const hit = [...document.querySelectorAll('#page .uc')]
          .find(c => c.textContent.includes('__acc_auto'));
        if (hit) return hit;
        await sleep(120);
      }
      return null;
    })();
    const autoCards = [...document.querySelectorAll('#page .uc')];
    R.autoCount = autoCards.length;
    R.autoHeights = [...new Set(autoCards.map(c => Math.round(c.getBoundingClientRect().height)))];
    const withDel = autoCard;
    R.autoFound = !!withDel;
    if (withDel) {
      withDel.querySelector('[data-ucact="autodel"]').click();
      await sleep(300);
      const m4 = q('#modal');
      R.autoConfirmTitle = m4.querySelector('.mhead h3').textContent;
      R.autoConfirmHasCancel = !!m4.querySelector('.mfoot [data-close]');
      R.autoConfirmHasOk = !!m4.querySelector('#ucOk');
      closeModal();
      await sleep(150);
    }
    // 收尾：把测试数据**全**删掉。
    // 之前只删 find() 找到的第一条 —— 中途失败时留下的会累积，
    // 下次验收页面上就有好几条 __acc_auto，find 挑到哪条就不确定了。
    // 注意变量名：块里已经有 list 了（会话列表那步），不能重名 ——
    // 重复 const 是语法错误，会让整个探针脚本不执行。
    const autoList = await (await fetch('/api/automations', { headers: { 'X-Oryx-Token': ORYX_TOKEN } })).json();
    for (const a of (autoList.automations || []).filter(x => x.name === '__acc_auto')) {
      await fetch('/api/automations/' + encodeURIComponent(a.id), {
        method: 'DELETE',
        headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1' },
      });
    }

    /* ── 8. 团队面板：编辑/解散/移出成员 按钮都在 ── */
    step = 'team-panel';
    // 自己建一个队 —— 界面上有没有团队取决于用户环境
    await fetch('/api/teams', {
      method: 'POST',
      headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: '__acc_team', goal: '验收用',
        members: ['app-agent:软件', 'search-agent:搜索'],
      }),
    });
    const teams = await (await fetch('/api/teams', { headers: { 'X-Oryx-Token': ORYX_TOKEN } })).json();
    R.teamCount = (teams.teams || []).length;
    R.accTeamExists = (teams.teams || []).some(t => t.name === '__acc_team');
    if (R.accTeamExists) {
      q('#teamBtn').click();
      await sleep(600);
      let tm = q('#modal');
      const pick = tm.querySelector('[data-tp="__acc_team"]');
      if (pick) { pick.click(); await sleep(900); tm = q('#modal'); }
      R.teamTitle = tm.querySelector('.mhead h3').textContent;
      R.teamHasEdit = !!tm.querySelector('#tpEdit');
      R.teamHasDel = !!tm.querySelector('#tpDel');
      R.teamHasDrop = !!tm.querySelector('[data-tpdrop]');
      R.teamHasRoleEdit = !!tm.querySelector('[data-tpedit]');
      if (tm.querySelector('[data-tpedit]')) {
        tm.querySelector('[data-tpedit]').click();
        await sleep(300);
        R.teamRoleEditOpen = !!q('#modal').querySelector('#trRole');
        closeModal();
        await sleep(150);
      }
      // 改分工的弹窗关掉后团队面板也一起没了 —— 重新打开
      q('#teamBtn').click();
      await sleep(600);
      let tm2 = q('#modal');
      const pick2 = tm2.querySelector('[data-tp="__acc_team"]');
      if (pick2) { pick2.click(); await sleep(900); tm2 = q('#modal'); }
      R.teamMemberCount = tm2 ? tm2.querySelectorAll('.member').length : -1;
      if (tm2 && tm2.querySelector('[data-tpdrop]')) {
        tm2.querySelector('[data-tpdrop]').click();
        await sleep(300);
        const m6 = q('#modal');
        R.teamDropConfirm = m6.querySelector('.mhead h3').textContent;
        R.teamDropHasCancel = !!m6.querySelector('.mfoot [data-close]');
        m6.querySelector('#ucOk').click();
        await sleep(1500);
        const tm3 = q('#modal');
        // 关键：确认后弹层要还在（不能被 ucConfirm 的 closeModal 关掉）
        R.teamPanelStillOpen = q('#mask').classList.contains('on');
        R.teamMembersAfter = tm3 ? tm3.querySelectorAll('.member').length : -1;
        closeModal();
      }
      // 收尾：解散测试队
      await fetch('/api/teams/__acc_team', {
        method: 'DELETE',
        headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1' },
      });
    }

    /* ── 9. 粘贴导入：后端接口 + 前端事件 ── */
    step = 'paste';
    const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    const pr = await fetch('/api/paste', {
      method: 'POST',
      headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'image', data_url: png, name: 'probe.png' }),
    });
    const pj = await pr.json();
    R.pasteApiOk = pr.ok && pj.ok === true;
    R.pasteKind = pj.item ? pj.item.kind : '';
    // 系统剪贴板接口（能读文件的那种）
    const cr = await fetch('/api/clipboard', { headers: { 'X-Oryx-Token': ORYX_TOKEN } });
    const cj = await cr.json();
    R.clipApiOk = cr.ok && typeof cj.kind === 'string';
    R.clipKind = cj.kind;
    const nFiles0 = S.files.length;
    const blob = await (await fetch(png)).blob();
    await pasteImage(new File([blob], 'probe2.png', { type: 'image/png' }));
    await sleep(600);
    R.pasteFilesGrew = S.files.length === nFiles0 + 1;
    R.pasteChips = document.querySelectorAll('#attbar .attchip').length;
    R.pasteBtnExists = !!q('#pasteBtn');
    S.files.length = 0; paintAttbar();

    /* ── 10. 停止按钮 ── */
    step = 'stop-btn';
    const sb = q('#sendBtn');
    R.sendBtnExists = !!sb;
    R.sendIdleIsArrow = !!q('#sendIcon path');
    // 跑着的时候变停止
    setSendState('running');
    R.stopHasClass = sb.classList.contains('stopping');
    R.stopHasSquare = !!q('#sendIcon rect');
    R.stopTitle = sb.title;
    R.stopNotDisabled = !sb.disabled;
    setSendState('idle');
    R.idleBackToArrow = !!q('#sendIcon path');
    R.idleNoStopClass = !sb.classList.contains('stopping');

    /* ── 11. 卡片不溢出 ── */
    step = 'card-overflow';
    setView('settings');
    const csec = await waitFor('#page [data-sec="capability"]');
    if (csec) csec.click();
    await waitFor('#capGrid .uc');
    const ccards = [...document.querySelectorAll('#capGrid .uc')];
    R.overflowCards = ccards.length;
    // 每一行的内容都要在卡片自己的盒子里
    R.noOverflow = ccards.every(c => {
      const cb = c.getBoundingClientRect();
      const foot = c.querySelector('.ufoot').getBoundingClientRect();
      const chips = c.querySelector('.uchips').getBoundingClientRect();
      const desc = c.querySelector('.udesc').getBoundingClientRect();
      return foot.bottom <= cb.bottom + 0.5
        && chips.bottom <= cb.bottom + 0.5
        && desc.bottom <= cb.bottom + 0.5
        && foot.bottom > cb.top;
    });
    R.overflowWorst = ccards.reduce((m, c) => {
      const cb = c.getBoundingClientRect();
      const foot = c.querySelector('.ufoot').getBoundingClientRect();
      return Math.max(m, foot.bottom - cb.bottom);
    }, -999);
    R.cardHeights = [...new Set(ccards.map(c => Math.round(c.getBoundingClientRect().height)))];

    /* ── 12. 新建对话位置 + 样式 ── */
    step = 'newchat';
    // 变量名别跟步骤 3 的 nc 重名 —— 同一个块里重复 const 是语法错误，
    // 而语法错误会让**整个探针脚本**不执行，页面看起来「什么都没发生」。
    const nc2 = q('.navitem.newchat');
    R.ncTop = nc2 ? Math.round(nc2.getBoundingClientRect().top) : -1;
    R.ncFirst = [...document.querySelectorAll('.railnav .navitem')][0] === nc2;
    R.ncBg = nc2 ? getComputedStyle(nc2).backgroundColor : '';
    R.ncSolid = nc2 ? getComputedStyle(nc2).backgroundColor !== 'rgba(0, 0, 0, 0)' : false;

    /* ── 13. 会话列表：最新在最上 ── */
    step = 'session-order';
    const rows = [...document.querySelectorAll('#sessList .sess')];
    R.sessionCount = rows.length;
    const sessData = await (await fetch('/api/sessions', { headers: { 'X-Oryx-Token': ORYX_TOKEN } })).json();
    const list = sessData.sessions || [];
    R.sessionOrderSorted = list.every((s, i) => i === 0
      || (list[i - 1].updated_at || list[i - 1].created_at || 0) >= (s.updated_at || s.created_at || 0));
    R.groupOrderFirst = q('#sessList .sgh') ? q('#sessList .sgh').textContent.trim() : '';

    /* ── 14. 真点「新建对话」──
       之前只查了它的位置和样式，从没真点过 —— 绑定坏了也发现不了。 */
    step = 'newchat-click';
    const ncx = q('.navitem.newchat');
    // 先离开对话页，让它有活可干
    setView('work');
    await sleep(500);
    R.ncViewBefore = S.view;
    ncx.click();
    await sleep(800);
    R.ncViewAfter = S.view;
    R.ncCurrentNull = S.current === null;
    R.ncTaskIdEmpty = S.taskId === '';
    R.ncCenterVisible = getComputedStyle(q('#center')).display !== 'none';
    R.ncInputFocused = document.activeElement === q('#input');
    R.ncTitle = q('#pageTitle').textContent.trim();

    /* ── 15. 跑着任务时点「新建对话」──
       发送键不能还停在红色停止态：点了会触发 stopRun() 而不是发消息。 */
    step = 'newchat-while-busy';
    S.busy = true; setSendState('running');
    addMsg('bot', '模拟一条正在跑的回答');
    await sleep(300);
    R.busyBeforeClick = S.busy;
    ncx.click();
    await sleep(700);
    R.busyAfterClick = S.busy;
    R.sendStoppingAfterClick = q('#sendBtn').classList.contains('stopping');
    R.sendIdleAfterClick = !!q('#sendIcon path');
    R.msgClearedAfterClick = document.querySelectorAll('#messages .msg').length === 0;

    /* ── 16. 别的任务的事件不往当前会话写 ── */
    step = 'foreign-event';
    const msgsBefore = document.querySelectorAll('#messages .msg').length;
    // 假装界面认领的是 task A，来一条 task B 的流式内容
    S.taskId = 'task_aaa';
    handle({ type: 'TEXT_MESSAGE_CONTENT', task_id: 'task_bbb', delta: '这条不该出现' });
    await sleep(200);
    R.foreignMsgIgnored = document.querySelectorAll('#messages .msg').length === msgsBefore;
    // 自己任务的内容要能进
    handle({ type: 'TEXT_MESSAGE_START', task_id: 'task_aaa' });
    handle({ type: 'TEXT_MESSAGE_CONTENT', task_id: 'task_aaa', delta: '这条应该出现' });
    await sleep(200);
    R.ownMsgAccepted = document.querySelectorAll('#messages .msg').length === msgsBefore + 1;
    R.ownMsgText = [...document.querySelectorAll('#messages .msg')].pop().textContent;

    /* ── 17. 终止类事件的判据 ── */
    step = 'terminal-event';
    R.foreignFinishedIgnored = isForeign({ type: 'RUN_FINISHED', task_id: 'task_bbb' }) === true;
    R.ownFinishedAccepted = isForeign({ type: 'RUN_FINISHED', task_id: 'task_aaa' }) === false;
    R.ownCancelledAccepted = isForeign({ type: 'RUN_CANCELLED', task_id: 'task_aaa' }) === false;
    R.teamEventNotScoped = isForeign({ type: 'team.message', task_id: 'task_bbb' }) === false;

    R.ok = true;
  } catch (e) {
    R.ok = false;
    R.step = step;
    R.error = String(e && e.stack || e);
  }
  // 断掉 SSE —— 它是长连接，不关的话 headless Chrome 永远不退出。
  try { if (es) { es.close(); es = null; } } catch (_) {}
  // 结果 POST 回代理服务。
  // 不用 --dump-dom 读结果 —— 那个模式要等页面所有资源都停，
  // 页面里有定时器与长连接时它会一直等到超时。
  try {
    await fetch('/__acc_result', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({}, R, { done: true })),
    });
  } catch (_) {}
  const d = document.createElement('div');
  d.id = 'acc-out';
  d.textContent = '__ACC__' + JSON.stringify(R);
  document.body.appendChild(d);
  document.title = '__ACC_DONE__';
})();
</script>
"""


def build_page(src: str, token: str) -> str:
    """真页面 + 验收脚本。

    令牌占位符要替换掉 —— 页面里的 __ORYX_TOKEN__ 由 server.py 注入，
    静态文件里还是占位符，不替换的话接口全 401。
    验收脚本插在 </body> 之前，保证 DOM 已经建好。
    """
    html = src.replace("__ORYX_TOKEN__", token)
    return html.replace("</body>", PROBE + "</body>")


class ProbeHandler(BaseHTTPRequestHandler):
    """把真页面与真后端接到一起的代理。

    同源是关键 —— 页面里的 fetch('/api/...') 要能打到后端，
    而后端的 CORS 白名单只放行本机来源，端口不同就算本机也会被挡。
    所以这里做转发而不是让页面直连 8765。
    """

    page_html = ""
    token = ""
    result: dict = {}          # 页面 POST 回来的验收结果

    def log_message(self, *a):  # 别刷屏
        pass

    def _proxy(self, method: str) -> None:
        url = BACKEND + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        # 令牌头补上 —— 代理在后端看来是可信的本地调用
        req.add_header("X-Oryx-Token", ProbeHandler.token)
        if method not in ("GET", "HEAD"):
            req.add_header("X-Oryx-Request", "1")
        if body is not None:
            req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                ctype = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            msg = json.dumps({"detail": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        data = ProbeHandler.page_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path == "/__acc_result":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {"ok": False, "error": "结果不是合法 JSON"}
            # 分段到达 —— 后到的覆盖先到的，但保留「曾经收到过」的标记
            ProbeHandler.result.update(data)
            if data.get("done"):
                ProbeHandler.result["done"] = True
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")


def start_probe_server(token: str) -> ThreadingHTTPServer:
    ProbeHandler.token = token
    ProbeHandler.page_html = build_page(WEB.read_text("utf-8"), token)
    srv = ThreadingHTTPServer(("127.0.0.1", PROBE_PORT), ProbeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(chrome: str, token: str) -> dict:
    """开 headless Chrome，等页面把结果 POST 回来。

    不用 --dump-dom —— 那个模式要等页面彻底停下来才吐 DOM，
    页面里有定时器和长连接时它会一直等到超时。
    改成「页面 POST 结果 → 代理服务收到 → 我们杀 Chrome」，
    时间可控得多。
    """
    import time

    tmp = Path(tempfile.mkdtemp(prefix="oryx_acc_"))
    ProbeHandler.result = {}
    srv = start_probe_server(token)
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--user-data-dir=" + str(tmp / "profile"),
            # **不要**加 --virtual-time-budget：
            # 页面里有 setInterval(refreshState, 6000)，虚拟时间会瞬间烧完预算，
            # 后面所有 setTimeout 都被冻结 —— 验收脚本会卡在第一个 sleep 上。
            # 用真实时间跑，等页面把结果 POST 回来。
            f"http://127.0.0.1:{PROBE_PORT}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if ProbeHandler.result.get("done"):
                return ProbeHandler.result
            if proc.poll() is not None:
                # Chrome 自己退了 —— 再等一下结果有没有送到
                time.sleep(1.0)
                return ProbeHandler.result or {
                    "ok": False,
                    "error": f"Chrome 提前退出（{proc.returncode}），没收到验收结果",
                }
            time.sleep(0.4)
        return {"ok": False, "error": "等验收结果超时（90 秒）", "partial": ProbeHandler.result}
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        srv.shutdown()


def check_probe_syntax() -> None:
    """先验一遍探针脚本的语法。

    这一步不能省：探针脚本是拼在页面里的**同一段 script 块**，
    语法错误会让整段不执行 —— 页面照常显示，验收结果却一个都收不到，
    表现是「等了 90 秒超时」，根因完全看不出来。
    之前就踩过：两个步骤里都声明了 const nc，重复声明直接语法错误。
    """
    import subprocess
    import tempfile

    node = shutil.which("node") or r"D:\Node.js\node.exe"
    if not Path(node).exists():
        print("  （跳过探针语法自检：找不到 node）")
        return
    js = PROBE.split("<script>", 1)[-1].split("</script>", 1)[0]
    tmp = Path(tempfile.mkdtemp(prefix="oryx_probe_")) / "probe.js"
    tmp.write_text(js, "utf-8")
    proc = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
    check("探针脚本语法正确", proc.returncode == 0, (proc.stderr or "")[:800])


def main() -> int:
    chrome = find_chrome()
    check("找得到 Chrome/Edge", bool(chrome))
    if not chrome:
        return 1

    check_probe_syntax()

    # 令牌：页面里的 __ORYX_TOKEN__ 由 server.py 注入，静态文件里是占位符。
    # 从正在跑的服务上问不出来（首页会带令牌），所以直接读配置。
    #
    # 配置可能在两个位置 —— 数据目录搬到 D 盘之后是 D:\OryxData\data，
    # 但老安装还在 C 盘留了一份。两个都试。
    token = os.environ.get("ORYX_API_TOKEN", "")
    if not token:
        try:
            import json as _json

            candidates = [
                Path("D:/OryxData/data/config.json"),
                Path(os.environ.get("APPDATA", "")) / "Oryx" / "config.json",
            ]
            for cfg_path in candidates:
                if cfg_path.exists():
                    token = str(_json.loads(cfg_path.read_text("utf-8")).get("api_token") or "")
                    if token:
                        break
        except Exception:
            token = ""
    check("拿得到访问令牌", bool(token))
    if not token:
        print("  设 ORYX_API_TOKEN，或确保 D:\\OryxData\\data\\config.json 里有 api_token")
        return 1

    src = WEB.read_text("utf-8")
    print("  （headless Chrome 加载真实页面，跑验收脚本）")
    r = run(chrome, token)
    if not r.get("ok"):
        print(f"  验收脚本没跑完（step={r.get('step')}）：{r.get('error')}")
        print("  已拿到的结果：", json.dumps({k: v for k, v in r.items() if k not in ('error', 'dom_head')}, ensure_ascii=False)[:1200])
        if r.get("dom_head"):
            print("  DOM 开头：", r["dom_head"][:300])
        return 1

    # ── 断言 ──
    check("左栏导航不滚动", r.get("navOverflow") == "visible" and not r.get("navScrollable"),
          f"overflow={r.get('navOverflow')} scrollable={r.get('navScrollable')}")
    sc = r.get("railScrollables") or []
    check("左栏只有会话列表有滚动条", all("sessList" in str(x) for x in sc) and sc, f"实际={sc}")

    check("点本地资料浮层弹出", r.get("subHidden") is False and r.get("subVisible"), str(r.get("subRect")))
    check("浮层不被卡片裁掉（fixed 定位）", r.get("subPosition") == "fixed", r.get("subPosition"))
    check("点本地资料不顶走对话列表", r.get("groupUnmoved") and r.get("sessUnmoved"),
          f"group={r.get('groupTop0')}->{r.get('groupTop1')} "
          f"sess={r.get('sessTop0')}->{r.get('sessTop1')} "
          f"navH={r.get('navH0')}->{r.get('navH1')} | {r.get('railParts')} "
          f"searchBox={r.get('searchBoxExists')} cardH={r.get('railcardH')}")
    check("浮层有四个入口", r.get("subItems") == ["知识库", "图片库", "应用库", "此电脑"], str(r.get("subItems")))

    check("新建对话是独立操作项", r.get("newchatExists") and r.get("newchatText") == "新建对话")
    check("新建对话不参与位置高亮", r.get("newchatOnAfterNav") is False and r.get("workOn") is True)

    check("能力页有卡片", r.get("capCount", 0) > 0, str(r.get("capCount")))
    check("能力卡片高度统一", len(r.get("capHeights") or []) == 1, str(r.get("capHeights")))
    check("同一行内描述与按钮对齐", r.get("capRowAlign") is True)
    check("描述折叠到两行", r.get("capClamp") == "2", r.get("capClamp"))
    check("主按钮文案统一为「在对话中使用」", r.get("capAllSameLabel"), r.get("capPrimaryLabel"))
    check("每张卡都有主按钮", r.get("capAllHavePrimary") is True)

    check("点卡片弹详情", r.get("capDetailOpen") is True)
    check("详情有内容行", len(r.get("capDetailRows") or []) >= 3, str(r.get("capDetailRows")))
    check("详情里能直接使用", r.get("capDetailHasUse") is True)

    check("技能卡高度统一", len(r.get("skillHeights") or []) == 1, str(r.get("skillHeights")))
    check("技能主按钮文案统一", r.get("skillPrimaryLabel") == "在对话中使用", r.get("skillPrimaryLabel"))
    check("点技能弹详情", r.get("skillDetailOpen") is True)
    check("技能详情能编辑能删除", r.get("skillDetailHasEdit") and r.get("skillDetailHasDel"))
    check("删技能有确认弹窗", r.get("skillConfirmOpen") is True and r.get("skillConfirmHasCancel"),
          r.get("skillConfirmTitle"))

    check("自动任务卡高度统一", len(r.get("autoHeights") or []) == 1, str(r.get("autoHeights")))
    check("删自动任务有确认弹窗", r.get("autoConfirmHasOk") and r.get("autoConfirmHasCancel"),
          r.get("autoConfirmTitle"))

    check("团队面板有编辑按钮", r.get("teamHasEdit") is True)
    check("团队面板有解散按钮", r.get("teamHasDel") is True)
    check("团队面板能移出成员", r.get("teamHasDrop") is True)
    check("团队面板能改分工", r.get("teamHasRoleEdit") is True)
    check("改分工弹窗能打开", r.get("teamRoleEditOpen") is True)
    check("移出成员有确认弹窗", r.get("teamDropHasCancel") is True, r.get("teamDropConfirm"))
    check("确认移出后面板不被误关", r.get("teamPanelStillOpen") is True)
    check("成员数确实减一",
          r.get("teamMembersAfter") == r.get("teamMemberCount", 0) - 1,
          f"{r.get('teamMemberCount')} -> {r.get('teamMembersAfter')}")

    check("粘贴接口可用", r.get("pasteApiOk") is True, str(r.get("pasteKind")))
    check("粘贴图片进附件栏", r.get("pasteFilesGrew") is True and r.get("pasteChips", 0) > 0,
          f"chips={r.get('pasteChips')}")
    check("有粘贴按钮", r.get("pasteBtnExists") is True)

    # ── 第十二批新增：停止 / 溢出 / 新建对话 / 会话排序 / 系统剪贴板 ──
    check("系统剪贴板接口可用", r.get("clipApiOk") is True, str(r.get("clipKind")))
    check("有停止按钮（发送键复用）", r.get("sendBtnExists") is True)
    check("空闲时是箭头", r.get("sendIdleIsArrow") is True)
    check("跑着时变成停止键", r.get("stopHasClass") is True and r.get("stopHasSquare") is True)
    check("停止键标题是「停止」", r.get("stopTitle") == "停止", r.get("stopTitle"))
    check("停止键可点（不被禁用）", r.get("stopNotDisabled") is True)
    check("停下来后变回发送箭头", r.get("idleBackToArrow") is True and r.get("idleNoStopClass") is True)

    check("卡片内容不溢出", r.get("noOverflow") is True, f"最多溢出 {r.get('overflowWorst')}px")
    check("卡片高度仍然统一", len(r.get("cardHeights") or []) == 1, str(r.get("cardHeights")))

    check("新建对话在导航第一位", r.get("ncFirst") is True)
    check("新建对话是实心块（对齐 Marvis）", r.get("ncSolid") is True, r.get("ncBg"))

    check("会话按最近动过排序", r.get("sessionOrderSorted") is True, str(r.get("sessionCount")))

    # ── 第十四批：新建对话点击 / 跑着时状态重置 / 事件按任务过滤 ──
    check("点新建对话能切回对话页", r.get("ncViewBefore") == "work" and r.get("ncViewAfter") == "chat",
          f"{r.get('ncViewBefore')} -> {r.get('ncViewAfter')}")
    check("点新建对话清掉当前会话", r.get("ncCurrentNull") is True)
    check("点新建对话不认领任何任务", r.get("ncTaskIdEmpty") is True)
    check("点新建对话后对话区可见", r.get("ncCenterVisible") is True)
    check("点新建对话后输入框聚焦", r.get("ncInputFocused") is True)
    check("点新建对话后标题是「新对话」", r.get("ncTitle") == "新对话", r.get("ncTitle"))

    check("跑着时点新建对话会清掉 busy", r.get("busyBeforeClick") is True and r.get("busyAfterClick") is False,
          f"{r.get('busyBeforeClick')} -> {r.get('busyAfterClick')}")
    check("跑着时点新建对话发送键变回箭头",
          r.get("sendStoppingAfterClick") is False and r.get("sendIdleAfterClick") is True)
    check("跑着时点新建对话清空消息区", r.get("msgClearedAfterClick") is True)

    check("别的任务的内容不进当前会话", r.get("foreignMsgIgnored") is True)
    check("自己任务的内容正常进来", r.get("ownMsgAccepted") is True, r.get("ownMsgText"))
    check("别的任务结束不收尾当前界面", r.get("foreignFinishedIgnored") is True)
    check("自己任务结束正常收尾", r.get("ownFinishedAccepted") is True)
    check("自己任务被停正常收尾", r.get("ownCancelledAccepted") is True)
    check("团队事件不受任务过滤影响", r.get("teamEventNotScoped") is True)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
