/* paste.js —— 分区 26。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { autoGrow, paintAttbar } from './models.js';
import { $, S, api, toast } from './core.js';

/* ══════ 粘贴导入 ══════
   粘贴能拿到什么：
     文件   用户在资源管理器里复制的文件 → **浏览器拿不到**（那是 Win32 的
             CF_HDROP），所以走后端 /api/clipboard 读系统剪贴板
     图片   剪贴板里的位图（截图软件写的）→ 同样走后端读，转 PNG 落盘
     文本   纯文字 → 短的直接进输入框，过长的落盘成文件
   为什么全走后端：浏览器那套 navigator.clipboard.read() 要权限、
   经常被拒，而且**永远读不到文件**。本机 Python 进程直接调 Win32 最稳。 */
const PASTE_TEXT_MAX = 2000;

/**
 * 从系统剪贴板导入。
 * kind: text / image / files / empty
 */
export async function importFromClipboard() {
  let d;
  try {
    d = await (await api('/api/clipboard')).json();
  } catch (e) {
    toast('读剪贴板失败：' + (e && e.message ? e.message : e));
    return;
  }
  const kind = d.kind || 'empty';
  if (kind === 'files') {
    const fresh = (d.files || []).filter(p => !S.files.includes(p));
    if (!fresh.length) { toast('这些文件已经在附件栏里了'); return; }
    S.files = S.files.concat(fresh);
    paintAttbar();
    const input = $('#input');
    const list = fresh.map(p => p.split(/[\\/]/).pop()).join('、');
    if (!input.value.trim()) input.value = '请处理这些文件：' + fresh.join('、');
    else if (!input.value.includes(list)) input.value += '\n' + list;
    input.focus();
    toast('已粘贴 ' + fresh.length + ' 个文件');
    return;
  }
  if (kind === 'image') {
    await pasteImageDataUrl(d.data_url);
    return;
  }
  if (kind === 'text') {
    const text = d.text || '';
    if (!text) { toast('剪贴板里没有文本'); return; }
    if (text.length <= PASTE_TEXT_MAX) {
      const input = $('#input');
      const start = input.selectionStart != null ? input.selectionStart : input.value.length;
      const end = input.selectionEnd != null ? input.selectionEnd : input.value.length;
      input.value = input.value.slice(0, start) + text + input.value.slice(end);
      input.focus();
      input.selectionStart = input.selectionEnd = start + text.length;
      autoGrow();
      toast('已粘贴 ' + text.length + ' 字');
      return;
    }
    await pasteText(text);
    return;
  }
  toast(d.detail || '剪贴板里没有可用的内容');
}

/** 处理一次粘贴（Ctrl+V）。走浏览器事件，只为拿图片与文本。 */
export async function handlePaste(ev) {
  const cd = ev.clipboardData;
  if (!cd) return;
  const target = ev.target;
  const inInput = target && target.id === 'input';

  // 先找图片 —— 截图粘贴到输入框也要能落盘
  for (const it of (cd.items || [])) {
    if (it.kind !== 'file' || !it.type || !it.type.startsWith('image/')) continue;
    const f = it.getAsFile();
    if (f) { ev.preventDefault(); await pasteImage(f); return; }
  }

  // 输入框里粘贴纯文字 → 交给输入框自己处理（保留撤销栈）
  if (inInput) return;

  ev.preventDefault();
  // 其余情况（尤其是文件）走后端读系统剪贴板
  await importFromClipboard();
}

/** 粘贴的图片落盘成附件。file 可以是 File，也可以是 data URL。 */
async function pasteImage(file) {
  try {
    const dataUrl = typeof file === 'string' ? file : await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = () => reject(new Error('读取失败'));
      fr.readAsDataURL(file);
    });
    await pasteImageDataUrl(dataUrl, file.name || 'paste.png');
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}

/** 图片 data URL 落盘并挂到附件栏。 */
async function pasteImageDataUrl(dataUrl, name) {
  try {
    const r = await api('/api/paste', {
      method: 'POST',
      body: JSON.stringify({ kind: 'image', data_url: dataUrl, name: name || 'paste.png' }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('粘贴失败：' + (e.detail || r.status)); return; }
    const item = (await r.json()).item;
    if (!S.files.includes(item.path)) S.files.push(item.path);
    paintAttbar();
    const input = $('#input');
    if (!input.value.trim()) input.value = '请看这张图：' + item.path;
    input.focus();
    toast('已粘贴图片 ' + item.name);
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}

/** 粘贴的长文本落盘成文件。 */
async function pasteText(text) {
  try {
    const r = await api('/api/paste', {
      method: 'POST',
      body: JSON.stringify({ kind: 'text', text, name: 'pasted.txt' }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('粘贴失败：' + (e.detail || r.status)); return; }
    const item = (await r.json()).item;
    if (!S.files.includes(item.path)) S.files.push(item.path);
    paintAttbar();
    const input = $('#input');
    if (!input.value.trim()) input.value = '请看这份内容：' + item.path;
    input.focus();
    toast('文本太长，已存成 ' + item.name);
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}
