// Novel Debug 前端逻辑

// ---- 工具函数 ----

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function showToast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isError ? ' error' : '');
  t.classList.remove('hidden');
  setTimeout(() => t.classList.add('hidden'), 3000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    ...(opts.body ? { body: JSON.stringify(opts.body) } : {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('json')) return res.json();
  return res.text();
}

/** 获取全局书名 */
function getBook() {
  return $('#global-book').value.trim() || null;
}

// ---- Tab 切换 ----

$$('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    $(`#tab-${btn.dataset.tab}`).classList.add('active');

    // 切换到轨迹构建器时加载历史 session
    if (btn.dataset.tab === 'trajectory') {
      if (!currentSessionId) {
        loadSessions();
        showSessionList();
      }
    }
  });
});

// ---- 侧边栏折叠/展开 ----

$('#btn-collapse-sidebar').addEventListener('click', () => {
  $('#session-sidebar').classList.add('collapsed');
  $('#btn-expand-sidebar').classList.remove('hidden');
});

$('#btn-expand-sidebar').addEventListener('click', () => {
  $('#session-sidebar').classList.remove('collapsed');
  $('#btn-expand-sidebar').classList.add('hidden');
});

function showSessionList() {
  $('#session-setup').classList.add('hidden');
  $('#session-workspace').classList.add('hidden');
  // 确保侧边栏展开
  $('#session-sidebar').classList.remove('collapsed');
  $('#btn-expand-sidebar').classList.add('hidden');
}

function showWorkspace() {
  $('#session-setup').classList.add('hidden');
  $('#session-workspace').classList.remove('hidden');
}

function showNewSessionForm() {
  $('#session-setup').classList.remove('hidden');
  $('#session-workspace').classList.add('hidden');
  $('#user-input').focus();
}

// ---- 书籍列表 ----

async function loadBooks() {
  try {
    const res = await api('/api/books');
    const sel = $('#global-book');
    sel.innerHTML = '';
    (res.books || []).forEach((b, i) => {
      const opt = document.createElement('option');
      opt.value = b;
      opt.textContent = b;
      if (i === 0) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (e) {
    console.warn('加载书籍列表失败:', e.message);
  }
}

// ---- 工具元数据 ----

let toolsMeta = [];

async function loadTools() {
  toolsMeta = await api('/api/tools');
  initPanels();
  renderTrajToolSelect();
}

function renderTrajToolSelect() {
  const sel = $('#traj-tool-select');
  sel.innerHTML = '';
  toolsMeta.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.name;
    opt.textContent = t.name;
    sel.appendChild(opt);
  });
  if (toolsMeta.length > 0) {
    renderParamForm(toolsMeta[0], '#traj-param-form');
  }
  sel.addEventListener('change', () => {
    const t = toolsMeta.find(x => x.name === sel.value);
    if (t) renderParamForm(t, '#traj-param-form');
  });
}

// ---- 参数表单渲染（隐藏 book 字段） ----

const HIDDEN_FIELDS = new Set(['book']);

function renderParamForm(toolMeta, containerSel) {
  const container = $(containerSel);
  const schema = toolMeta.params_schema;
  const props = schema.properties || {};
  const required = schema.required || [];

  let html = '';

  // 工具描述折叠区
  const desc = toolMeta.description || '';
  if (desc) {
    html += `<button class="tool-desc-toggle" onclick="toggleToolDesc(this)">
      <span class="arrow">▶</span> 工具描述
    </button>
    <div class="tool-desc-wrapper">
      <div class="tool-desc-content">${escapeHtml(desc)}</div>
    </div>`;
  }

  const fieldOrder = Object.keys(props).filter(n => !HIDDEN_FIELDS.has(n));
  fieldOrder.sort((a, b) => {
    const ar = required.includes(a) ? 0 : 1;
    const br = required.includes(b) ? 0 : 1;
    return ar - br;
  });

  for (const name of fieldOrder) {
    const prop = props[name];
    const isReq = required.includes(name);
    const label = `${name}${isReq ? ' *' : ''}`;
    const paramDesc = prop.description || '';

    if (prop.enum) {
      html += `<div class="form-group">
        <label>${label}</label>
        <select name="${name}">
          ${prop.enum.map(v => `<option value="${v}">${v}</option>`).join('')}
        </select>
        ${paramDesc ? `<div class="param-desc">${escapeHtml(paramDesc)}</div>` : ''}
      </div>`;
    } else if (prop.type === 'integer') {
      const def = prop.default ?? '';
      html += `<div class="form-group">
        <label>${label}</label>
        <input type="number" name="${name}" value="${def}" ${isReq ? 'required' : ''}>
        ${paramDesc ? `<div class="param-desc">${escapeHtml(paramDesc)}</div>` : ''}
      </div>`;
    } else if (prop.type === 'array') {
      const def = prop.default ?? '';
      html += `<div class="form-group">
        <label>${label}（逗号分隔）</label>
        <input type="text" name="${name}" value="${def}" placeholder="item1,item2">
        ${paramDesc ? `<div class="param-desc">${escapeHtml(paramDesc)}</div>` : ''}
      </div>`;
    } else {
      const def = prop.default ?? '';
      html += `<div class="form-group">
        <label>${label}</label>
        <input type="text" name="${name}" value="${def}" ${isReq ? 'required' : ''}>
        ${paramDesc ? `<div class="param-desc">${escapeHtml(paramDesc)}</div>` : ''}
      </div>`;
    }
  }

  container.innerHTML = html;
}

function toggleToolDesc(btn) {
  const wrapper = btn.nextElementSibling;
  btn.classList.toggle('expanded');
  wrapper.classList.toggle('expanded');
}

function collectParams(containerSel) {
  const container = $(containerSel);
  const inputs = container.querySelectorAll('input, select, textarea');
  const params = {};

  inputs.forEach(el => {
    const name = el.name;
    let val = el.value.trim();
    if (!val) return;

    if (el.type === 'number') {
      val = parseInt(val, 10);
    } else if (el.closest('.form-group')?.querySelector('label')?.textContent.includes('逗号分隔')) {
      val = val.split(',').map(s => {
        const n = parseInt(s.trim(), 10);
        return isNaN(n) ? s.trim() : n;
      });
    }
    params[name] = val;
  });

  return params;
}

// ---- Tab 1: 统一三面板视图 ----

/** 解析工具返回的 output 文本 */
function parseOutput(result) {
  let output = result.output || '';
  if (Array.isArray(output)) {
    output = output.map(p => p.text || JSON.stringify(p)).join('\n');
  }
  return output;
}

/** 渲染通用结果 */
function renderResult(resultEl, result) {
  const output = parseOutput(result);
  const msg = result.message || '';
  const brief = result.brief || '';
  const isError = result.is_error;

  resultEl.innerHTML = `<div style="color:${isError ? '#e25c4d' : '#4dab6f'};font-weight:bold;margin-bottom:8px">${isError ? '错误' : '成功'}${brief ? ' - ' + brief : ''}</div>${escapeHtml(output)}${msg ? `<div style="margin-top:8px;color:#787774;border-top:1px solid #e8e5e0;padding-top:8px">${escapeHtml(msg)}</div>` : ''}`;
}

/** 初始化面板事件 */
function initPanels() {
  // 面板折叠
  $$('.panel-header').forEach(header => {
    header.addEventListener('click', () => {
      const panel = header.parentElement;
      panel.classList.toggle('collapsed');
    });
  });

  // Entity 搜索
  $('#btn-entity-search').addEventListener('click', searchEntity);
  $('#entity-query').addEventListener('keydown', e => {
    if (e.key === 'Enter') searchEntity();
  });

  // Graph 查询
  $('#btn-graph-query').addEventListener('click', queryGraph);

  // Corpus 搜索
  $('#btn-corpus-search').addEventListener('click', searchCorpus);

  // Chapter 读取
  $('#btn-chapter-read').addEventListener('click', readChapter);
}

// ---- Entity 面板 ----

async function searchEntity() {
  const query = $('#entity-query').value.trim();
  if (!query) return showToast('请输入搜索关键词', true);

  const params = {
    query,
    search_mode: $('#entity-mode').value,
    top_k: parseInt($('#entity-topk').value, 10) || 10,
  };

  const btn = $('#btn-entity-search');
  btn.disabled = true;
  btn.textContent = '搜索中...';

  try {
    const result = await api('/api/tools/SearchEntity/call', {
      method: 'POST',
      body: { params, book: getBook() },
    });

    const resultEl = $('#entity-result');

    if (result.is_error) {
      renderResult(resultEl, result);
      updateSummary('entity', '查询失败');
      return;
    }

    const output = parseOutput(result);
    const entities = parseEntityResults(output);
    renderEntityResults(resultEl, entities, output);
    updateSummary('entity', `${entities.length} 条结果`);
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '搜索';
  }
}

/** 从 output 文本中解析实体列表 */
function parseEntityResults(output) {
  const entities = [];
  // 按实体块分割，格式：[N] entity_id 后跟名称/类型行
  const blocks = output.split(/\n(?=\[\d+\])/);
  for (const block of blocks) {
    // 匹配 [N] entity_id
    const idMatch = block.match(/^\[(\d+)\]\s+(\S+)/);
    if (!idMatch) continue;
    const id = idMatch[2];
    // 匹配 名称: xxx | 类型: xxx
    const infoMatch = block.match(/名称:\s*(\S+)(?:\s*\|\s*类型:\s*(\S+))?/);
    const name = infoMatch ? infoMatch[1] : id;
    const type = infoMatch ? (infoMatch[2] || '') : '';
    entities.push({ id, name, type });
  }
  return entities;
}

/** 渲染 Entity 结果列表 */
function renderEntityResults(resultEl, entities, rawOutput) {
  if (entities.length === 0) {
    // 没有解析出 entity_id，直接显示原始输出
    resultEl.innerHTML = `<pre style="white-space:pre-wrap">${escapeHtml(rawOutput)}</pre>`;
    return;
  }

  let html = '';
  entities.forEach(e => {
    html += `<div class="entity-item" data-entity-id="${escapeHtml(e.id)}" title="点击填充到 Graph 面板">
      <span class="entity-name">${escapeHtml(e.name)}</span>
      <span class="entity-type">${escapeHtml(e.type)}</span>
    </div>`;
  });

  // 可展开的原始输出
  html += `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:#787774">原始输出</summary><pre style="white-space:pre-wrap;font-size:12px;margin-top:4px">${escapeHtml(rawOutput)}</pre></details>`;

  resultEl.innerHTML = html;

  // 绑定联动：点击实体名 → 填充 Graph
  resultEl.querySelectorAll('.entity-item').forEach(item => {
    item.addEventListener('click', () => {
      const entityId = item.dataset.entityId;
      $('#graph-entity-id').value = entityId;
      showToast(`已填充 entity_id: ${entityId}`);
    });
  });
}

// ---- Graph 面板 ----

async function queryGraph() {
  const entityId = $('#graph-entity-id').value.trim();
  if (!entityId) return showToast('请输入 entity_id', true);

  const params = { entity_id: entityId };

  const relType = $('#graph-rel-type').value.trim();
  if (relType) {
    params.rel_type = relType;
  }

  const btn = $('#btn-graph-query');
  btn.disabled = true;
  btn.textContent = '查询中...';

  try {
    const result = await api('/api/tools/SearchGraph/call', {
      method: 'POST',
      body: { params, book: getBook() },
    });

    const resultEl = $('#graph-result');
    renderResult(resultEl, result);
    updateSummary('graph', result.is_error ? '查询失败' : '已查询');
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '查询';
  }
}

// ---- Corpus 面板 ----

async function searchCorpus() {
  const keyword = $('#corpus-keywords').value.trim();
  const chapterIds = $('#corpus-chapter-ids').value.trim();

  if (!keyword && !chapterIds) return showToast('请输入 keyword 或 chapter_ids', true);

  const params = {};

  if (keyword) {
    params.keyword = keyword;
  }
  if (chapterIds) {
    params.chapter_ids = chapterIds.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
  }

  const contextSize = parseInt($('#corpus-context').value, 10);
  if (contextSize > 0) {
    params.context_size = contextSize;
  }

  const btn = $('#btn-corpus-search');
  btn.disabled = true;
  btn.textContent = '搜索中...';

  try {
    const result = await api('/api/tools/SearchCorpus/call', {
      method: 'POST',
      body: { params, book: getBook() },
    });

    const resultEl = $('#corpus-result');
    renderResult(resultEl, result);
    updateSummary('corpus', result.is_error ? '查询失败' : '已查询');
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '搜索';
  }
}

// ---- Chapter 面板 ----

async function readChapter() {
  const chapterId = $('#chapter-id').value.trim();
  if (!chapterId) return showToast('请输入 chapter_id', true);

  const params = { chapter_id: parseInt(chapterId, 10) };

  const startVal = $('#chapter-start').value.trim();
  const endVal = $('#chapter-end').value.trim();
  if (startVal && endVal) {
    params.start = parseInt(startVal, 10);
    params.end = parseInt(endVal, 10);
  }

  const btn = $('#btn-chapter-read');
  btn.disabled = true;
  btn.textContent = '读取中...';

  try {
    const result = await api('/api/tools/ReadChapter/call', {
      method: 'POST',
      body: { params, book: getBook() },
    });

    const resultEl = $('#chapter-result');
    renderResult(resultEl, result);
    updateSummary('chapter', result.is_error ? '读取失败' : '已读取');
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '读取';
  }
}

// ---- 面板辅助函数 ----

function updateSummary(panel, text) {
  $(`#${panel}-summary`).textContent = text;
}

function toggleAdvanced(panel) {
  const el = $(`#${panel}-advanced`);
  el.classList.toggle('expanded');
  const btn = el.previousElementSibling;
  btn.textContent = el.classList.contains('expanded') ? '▼ 高级选项' : '▶ 高级选项';
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

/** 复制文本到剪贴板（兼容 HTTP 环境） */
function copyToClipboard(text) {
  if (navigator.clipboard) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;left:-9999px';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

// ---- Tab 2: 轨迹构建器 ----

let currentSessionId = null;

// 更新侧边栏当前会话的调用次数（delta: +1 或 -1）
function updateSessionToolCount(delta) {
  const li = document.querySelector(`#session-list li[data-session-id="${currentSessionId}"]`);
  if (!li) return;
  const span = li.querySelector('.session-meta-row span:first-child');
  if (!span) return;
  const count = parseInt(span.textContent) || 0;
  span.textContent = `${count + delta} 次调用`;
}

// 加载历史 session 列表
async function loadSessions() {
  try {
    const sessions = await api('/api/sessions');
    const ul = $('#session-list');
    ul.innerHTML = '';

    if (sessions.length === 0) {
      ul.innerHTML = '<li style="color:#b4b4b0;cursor:default;padding:16px;text-align:center">暂无历史会话</li>';
      return;
    }

    sessions.forEach(s => {
      const li = document.createElement('li');
      li.dataset.sessionId = s.session_id;
      if (s.session_id === currentSessionId) li.classList.add('active');
      li.innerHTML = `
        <div class="session-header-row">
          <span class="session-name">${escapeHtml(s.session_id)}</span>
          <button class="btn-delete-session" title="删除会话" onclick="event.stopPropagation()">✕</button>
        </div>
        <span class="session-question">${escapeHtml(s.user_input || '(无问题)')}</span>
        <div class="session-meta-row">
          <span>${s.tool_count} 次调用</span>
          <span class="session-badge ${s.completed ? 'completed' : 'active'}">${s.completed ? '已结束' : '进行中'}</span>
        </div>
      `;
      li.addEventListener('click', () => restoreSession(s.session_id));
      // 删除按钮绑定
      li.querySelector('.btn-delete-session').addEventListener('click', (e) => {
        e.stopPropagation();
        deleteSession(s.session_id);
      });
      ul.appendChild(li);
    });
  } catch (e) {
    console.warn('加载会话列表失败:', e.message);
  }
}

// 删除会话
async function deleteSession(sessionId) {
  if (!confirm(`确定删除会话 "${sessionId}"？`)) return;
  try {
    await api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    showToast('已删除');
    if (currentSessionId === sessionId) {
      currentSessionId = null;
      $('#trajectory-timeline').innerHTML = '';
      $('#btn-undo').disabled = true;
      showSessionList();
    }
    loadSessions();
  } catch (e) {
    showToast(e.message, true);
  }
}

// 恢复历史 session
async function restoreSession(sessionId) {
  try {
    const res = await api(`/api/sessions/${sessionId}/timeline`);
    currentSessionId = res.session_id;

    // 高亮当前选中
    $$('#session-list li').forEach(li => li.classList.toggle('active', li.dataset.sessionId === sessionId));

    // 渲染时间线
    const timeline = $('#trajectory-timeline');
    timeline.innerHTML = '';
    res.events.forEach(ev => {
      timeline.innerHTML += renderTimelineItem(ev.type, ev.label, ev.content);
    });
    timeline.scrollTop = timeline.scrollHeight;

    showWorkspace();

    // 有任何时间线内容就可以撤回
    $('#btn-undo').disabled = res.events.length === 0;

    showToast(`已加载会话: ${sessionId}`);
    // 兜底：从后端刷新整个会话列表，确保计数准确
    loadSessions();
  } catch (e) {
    showToast(e.message, true);
  }
}

// "新建会话"按钮
$('#btn-new-session').addEventListener('click', showNewSessionForm);

$('#btn-start-session').addEventListener('click', async () => {
  const userInput = $('#user-input').value.trim();
  if (!userInput) return showToast('请输入问题', true);

  try {
    const res = await api('/api/sessions', {
      method: 'POST',
      body: { user_input: userInput, book: getBook() },
    });

    currentSessionId = res.session_id;
    showWorkspace();
    loadSessions();

    $('#trajectory-timeline').innerHTML = renderTimelineItem('user', '用户', userInput);
    showToast(`会话已创建: ${currentSessionId}`);
  } catch (e) {
    showToast(e.message, true);
  }
});

// 调用工具
$('#btn-call-tool').addEventListener('click', async () => {
  if (!currentSessionId) return showToast('请先创建会话', true);

  const toolName = $('#traj-tool-select').value;
  const params = collectParams('#traj-param-form');

  const btn = $('#btn-call-tool');
  btn.disabled = true;
  btn.textContent = '调用中...';

  try {
    const res = await api(`/api/sessions/${currentSessionId}/tool-call`, {
      method: 'POST',
      body: { tool_name: toolName, arguments: params, book: getBook() },
    });

    const timeline = $('#trajectory-timeline');
    const argsStr = Object.entries(params).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    timeline.innerHTML += renderTimelineItem('tool-call', `${toolName}(${argsStr})`, '');

    let output = res.result.output || '';
    if (Array.isArray(output)) {
      output = output.map(p => p.text || JSON.stringify(p)).join('\n');
    }
    const isError = res.result.is_error;

    timeline.innerHTML += renderTimelineItem(
      `tool-result${isError ? ' is-error' : ''}`,
      `→ ${output}`,
      ''
    );

    timeline.scrollTop = timeline.scrollHeight;
    $('#btn-undo').disabled = false;
    updateSessionToolCount(+1);
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '调用';
  }
});

// 撤回
$('#btn-undo').addEventListener('click', async () => {
  if (!currentSessionId) return;

  try {
    const res = await api(`/api/sessions/${currentSessionId}/undo`, { method: 'POST' });
    const timeline = $('#trajectory-timeline');

    // ContentPart / TurnEnd 撤回 1 条，ToolCall+ToolResult 撤回 2 条
    const removeCount = res.message.includes('工具调用') ? 2 : 1;
    if (res.message.includes('工具调用')) updateSessionToolCount(-1);
    for (let i = 0; i < removeCount; i++) {
      const last = timeline.querySelector('.timeline-item:last-child');
      if (last) last.remove();
    }

    // 有剩余 tool-call 才保留撤回按钮
    const remaining = timeline.querySelectorAll('.timeline-item.tool-call');
    $('#btn-undo').disabled = remaining.length === 0;
    showToast(res.message);
  } catch (e) {
    showToast(e.message, true);
  }
});

// 提交模型回答
$('#btn-submit-text').addEventListener('click', async () => {
  if (!currentSessionId) return;

  const text = $('#model-text').value.trim();
  if (!text) return showToast('请输入模型回答', true);

  try {
    await api(`/api/sessions/${currentSessionId}/text`, {
      method: 'POST',
      body: { text },
    });

    const timeline = $('#trajectory-timeline');
    timeline.innerHTML += renderTimelineItem('text-part', '模型回答', text.substring(0, 200) + (text.length > 200 ? '...' : ''));
    timeline.scrollTop = timeline.scrollHeight;

    $('#model-text').value = '';
    showToast('回答已提交');
  } catch (e) {
    showToast(e.message, true);
  }
});

// 复制轨迹
$('#btn-copy-traj').addEventListener('click', async () => {
  if (!currentSessionId) return;

  try {
    const res = await api(`/api/sessions/${currentSessionId}/copy`);
    copyToClipboard(res.text);
    showToast('轨迹已复制到剪贴板');
  } catch (e) {
    showToast(e.message, true);
  }
});

// 预测下一步（带工具定义的轨迹）
$('#btn-copy-traj-tools').addEventListener('click', async () => {
  if (!currentSessionId) return;

  try {
    const res = await api(`/api/sessions/${currentSessionId}/copy-with-tools`);
    copyToClipboard(res.text);
    showToast('预测上下文已复制到剪贴板');
  } catch (e) {
    showToast(e.message, true);
  }
});

// 结束轮次
$('#btn-end-turn').addEventListener('click', async () => {
  if (!currentSessionId) return;

  try {
    await api(`/api/sessions/${currentSessionId}/end`, { method: 'POST' });

    currentSessionId = null;
    $('#user-input').value = '';
    $('#trajectory-timeline').innerHTML = '';
    $('#btn-undo').disabled = true;
    loadSessions();
    showSessionList();
    showToast('轮次已结束，wire.jsonl 已保存');
  } catch (e) {
    showToast(e.message, true);
  }
});

// ---- 时间线渲染 ----

function renderTimelineItem(type, label, content) {
  return `<div class="timeline-item ${type}">
    <div class="label">${escapeHtml(label)}</div>
    ${content ? `<div class="content">${escapeHtml(content)}</div>` : ''}
  </div>`;
}

// ---- 初始化 ----

loadBooks();
loadTools().catch(e => showToast('加载工具失败: ' + e.message, true));
