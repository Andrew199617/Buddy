/* Actual per-run usage and responsive chat details for the packaged WebUI. */
(function () {
 'use strict';
 function tokenCount(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const count = Number(value);
  if (!Number.isSafeInteger(count) || count < 0) return null;
  return count;
 }
 function firstCount(values) {
  for (const value of values) {
   const count = tokenCount(value);
   if (count !== null) return count;
  }
  return null;
 }
 function runUsage(message) {
  const recorded = message?.meta?.local_run;
  const usage = message?.usage ?? message?.info?.usage ?? message?.info ?? {};
  let input;
  let output;
  let total;
  if (recorded && recorded.version === 1) {
   input = tokenCount(recorded.input_tokens);
   output = tokenCount(recorded.output_tokens);
   total = tokenCount(recorded.total_tokens);
  } else {
   input = firstCount([usage.input_tokens, usage.prompt_tokens, usage.prompt_eval_count]);
   if (input === null && (usage.prompt_n != null || usage.cache_n != null)) {
    input = (tokenCount(usage.prompt_n) ?? 0) + (tokenCount(usage.cache_n) ?? 0);
   }
   output = firstCount([usage.output_tokens, usage.completion_tokens, usage.eval_count, usage.predicted_n]);
   total = tokenCount(usage.total_tokens);
  }
  const staleAfterError = recorded?.status === 'completed' && Boolean(message?.error);
  if (staleAfterError) {
   input = null;
   output = null;
   total = null;
  }
  if (!recorded && total > 0 && input === 0 && output === 0) {
   input = null;
   output = null;
  }
  if (total === null && input !== null && output !== null) total = input + output;
  return {
   input: input, output: output, total: total,
   recorded: recorded?.version === 1,
   staleAfterError: staleAfterError,
   complete: recorded?.usage_complete !== false && !staleAfterError,
   status: staleAfterError ? 'error' : recorded?.status ?? 'completed',
   startedAt: staleAfterError ? null : recorded?.started_at ?? message?.timestamp ?? null,
   completedAt: staleAfterError ? null : recorded?.completed_at ?? null,
   durationMs: staleAfterError ? null : tokenCount(recorded?.duration_ms ?? usage.duration_ms),
   modelId: recorded?.model_id ?? message?.selectedModelId ?? message?.model ?? null
  };
 }
 function activeMessages(chat) {
  const history = chat?.chat?.history;
  if (!history?.messages || !history.currentId) return [];
  const messages = [];
  const visited = new Set();
  let id = history.currentId;
  while (id && history.messages[id] && !visited.has(id)) {
   visited.add(id);
   const message = history.messages[id];
   messages.unshift(message);
   id = message.parentId;
  }
  return messages;
 }
 function latestResponse(chat) {
  const messages = activeMessages(chat);
  for (let index = messages.length - 1; index >= 0; index -= 1) {
   if (messages[index].role === 'assistant') return messages[index];
  }
  return null;
 }
 function advertisedCapacity(model) {
  const configured = firstCount([model?.info?.params?.num_ctx, model?.params?.num_ctx]);
  if (configured !== null && configured > 0) return { tokens: configured, source: 'configured' };
  const advertised = firstCount([
   model?.context_length, model?.context_window, model?.max_context_tokens,
   model?.info?.meta?.context_length, model?.info?.meta?.context_window,
   model?.info?.meta?.max_context_tokens, model?.openai?.context_length
  ]);
  if (advertised !== null && advertised > 0) return { tokens: advertised, source: 'advertised' };
  return null;
 }
 function contextInfo(chat, model) {
  const response = latestResponse(chat);
  const recorded = response?.meta?.local_run;
  const supplied = chat?.context_usage;
  let tokens = firstCount([supplied?.estimated_tokens, supplied?.tokens]);
  let source = 'Estimated current context';
  if (tokens === null) {
   tokens = tokenCount(recorded?.context_tokens);
   source = 'Last request and response';
  }
  if (tokens === null) {
   const usage = response?.usage ?? response?.info?.usage ?? response?.info ?? {};
   const input = firstCount([usage.prompt_tokens, usage.prompt_eval_count]);
   const output = firstCount([usage.completion_tokens, usage.eval_count]);
   if (input !== null && output !== null) tokens = input + output;
  }
  let capacity = null;
  const chatCapacity = tokenCount(chat?.chat?.params?.num_ctx);
  if (chatCapacity > 0) capacity = { tokens: chatCapacity, source: 'configured' };
  if (!capacity) capacity = advertisedCapacity(model);
  const savedCapacity = recorded?.context_capacity;
  if (!capacity && (!model || model.id === recorded?.model_id) && tokenCount(savedCapacity?.tokens) > 0) {
   capacity = { tokens: tokenCount(savedCapacity.tokens), source: savedCapacity.source };
  }
  let percent = null;
  if (capacity && tokens !== null) percent = Math.round(tokens / capacity.tokens * 100);
  return { tokens: tokens, source: source, capacity: capacity, threshold: tokenCount(supplied?.threshold), percent: percent };
 }
 function compactCount(value) {
  if (value === null || value === undefined) return 'Unavailable';
  if (value < 1000) return String(value);
  let divisor = 1000;
  let unit = 'K';
  if (value >= 1000000) { divisor = 1000000; unit = 'M'; }
  return `${(value / divisor).toFixed(1).replace(/\.0$/, '')}${unit}`;
 }
 function durationText(value) {
  if (value === null || value === undefined) return 'Unavailable';
  if (value < 1000) return `${value} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')} s`;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.round((value % 60000) / 1000);
  return `${minutes} min ${seconds} s`;
 }
 if (typeof module !== 'undefined' && module.exports) {
  module.exports = { tokenCount, runUsage, activeMessages, latestResponse, advertisedCapacity, contextInfo, compactCount, durationText };
 }
 if (typeof window === 'undefined' || window.__owuiChatUsageInfo) return;
 window.__owuiChatUsageInfo = true;
 const css = `
.owui-context-rail { display:flex; justify-content:flex-end; padding:0 8px 5px; min-width:0; }
.owui-context-badge { display:inline-flex; align-items:center; gap:6px; min-height:40px; max-width:100%; padding:4px 8px; border-radius:10px; color:#6b7280; font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px)); line-height:1.4; background:transparent; cursor:pointer; }
.owui-context-badge:hover, .owui-run-footer:hover { background:rgba(0,0,0,.035); color:#111827; }
.owui-context-badge svg { width:16px; height:16px; flex-shrink:0; }
.owui-run-footer { display:flex; flex-wrap:wrap; justify-content:flex-end; align-items:center; gap:5px; width:fit-content; max-width:100%; min-height:40px; margin:3px 0 4px auto; padding:4px 6px; border-radius:8px; color:#6b7280; font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px)); line-height:1.5; cursor:pointer; text-align:end; }
.owui-run-footer .owui-run-model { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:220px; }
.owui-run-footer .owui-run-count, .owui-run-footer time { white-space:nowrap; }
.owui-run-enhanced .buttons time, .owui-run-enhanced .buttons button[id^="info-"] { display:none; }
.owui-usage-preview-panel { display:flex; flex-direction:column; max-height:min(24rem,calc(100dvh - 1.5rem)) !important; }
.owui-usage-preview-panel > div:first-child, .owui-usage-preview { flex-shrink:0; }
.owui-usage-preview-panel [id^="chat-hover-preview-messages-"] { min-height:0; flex:1 1 auto; overflow-y:auto; max-height:min(15rem,calc(100dvh - 10rem)) !important; }
.owui-usage-preview { padding:8px 12px; border-bottom:1px solid #e5e7eb; font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px)); line-height:1.5; color:#6b7280; }
.owui-usage-preview strong { display:block; font-weight:500; color:#374151; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.owui-chat-details-action { min-height:40px !important; }
.owui-chat-details-action svg { width:16px; height:16px; }
.owui-usage-overlay { position:fixed; inset:0; z-index:10020; pointer-events:none; }
.owui-usage-panel { position:absolute; pointer-events:auto; width:360px; max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); overflow:auto; overscroll-behavior:contain; padding:16px; border:1px solid #e5e7eb; border-radius:18px; background:#fff; color:#111827; box-shadow:0 16px 48px rgba(0,0,0,.16); font-size: calc(.875rem + var(--buddy-font-size-offset, 2px)); line-height:1.5; }
.owui-usage-header { position:sticky; top:-16px; z-index:1; background:inherit; display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
.owui-usage-header h2 { margin:0; font-size: calc(1rem + var(--buddy-font-size-offset, 2px)); font-weight:600; }
.owui-usage-close { display:flex; align-items:center; justify-content:center; width:40px; height:40px; flex-shrink:0; border-radius:10px; }
.owui-usage-close:hover { background:#f3f4f6; }
.owui-usage-close svg { width:20px; height:20px; }
.owui-usage-subtitle { margin:0 0 14px; font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px)); color:#6b7280; overflow-wrap:anywhere; }
.owui-usage-rows { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:9px 16px; margin:0; }
.owui-usage-rows dt { color:#6b7280; }
.owui-usage-rows dd { margin:0; text-align:end; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; max-width:220px; }
.owui-usage-note { margin:14px 0 0; font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px)); line-height:1.5; color:#6b7280; }
.owui-context-meter { height:6px; background:#e5e7eb; border-radius:6px; overflow:hidden; margin:14px 0; }
.owui-context-meter > span { display:block; height:100%; background:#0284c7; border-radius:6px; }
.owui-usage-panel button:focus-visible, .owui-context-badge:focus-visible, .owui-run-footer:focus-visible { outline:2px solid #0284c7; outline-offset:2px; }
.owui-usage-overlay.owui-usage-sheet { pointer-events:auto; background:rgba(0,0,0,.28); }
.owui-usage-sheet .owui-usage-panel { width:100%; max-width:100%; border-radius:24px 24px 0 0; border-bottom:0; padding:20px 20px max(20px,env(safe-area-inset-bottom)); font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px)); }
.owui-usage-sheet .owui-usage-header { top:-20px; }
.owui-usage-sheet .owui-usage-close { width:44px; height:44px; }
html.owui-usage-sheet-open, html.owui-usage-sheet-open body { overflow:hidden; }
.dark .owui-context-badge, .dark .owui-run-footer, .dark .owui-usage-preview { color:#9ca3af; }
.dark .owui-context-badge:hover, .dark .owui-run-footer:hover { color:#f3f4f6; background:rgba(255,255,255,.05); }
.dark .owui-usage-panel { color:#f3f4f6; background:#171717; border-color:#374151; }
.dark .owui-usage-subtitle, .dark .owui-usage-rows dt, .dark .owui-usage-note { color:#9ca3af; }
.dark .owui-usage-close:hover, .dark .owui-context-meter { background:#374151; }
.dark .owui-usage-preview { border-color:#374151; }
.dark .owui-usage-preview strong { color:#d1d5db; }
@media (max-width:767px), (pointer:coarse) {
 .owui-run-footer .owui-run-model { max-width:140px; }
 .owui-chat-details-action { min-height:44px !important; font-size: calc(.875rem + var(--buddy-font-size-offset, 2px)) !important; }
}
`;
 const INFO_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg>';
 const CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
 const originalFetch = window.fetch;
 const chats = new Map();
 const models = new Map();
 const pendingReads = new Map();
 const readTimes = new Map();
 const knownDone = new Set();
 let scheduledFrame = null;
 let activeId;
 let contextBadge = null;
 let panelState = null;
 let refreshTimer = null;
 let hoverTimer = null;
 let closeTimer = null;
 let menuChatId = null;
 let menuTrigger = null;
 function currentChatId() {
  const match = window.location.pathname.match(/\/c\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : null;
 }
 function setText(element, value) {
  if (element.textContent !== value) element.textContent = value;
 }
 function cacheChat(chat) {
  if (!chat?.id || !chat?.chat?.history) return;
  chats.delete(chat.id);
  chats.set(chat.id, chat);
  while (chats.size > 30) {
   const oldestId = chats.keys().next().value;
   chats.delete(oldestId);
   readTimes.delete(oldestId);
  }
  readTimes.set(chat.id, Date.now());
  scheduleUpdate();
  if (panelState?.chatId === chat.id) renderPanel();
 }
 function cacheModels(payload) {
  const list = Array.isArray(payload) ? payload : payload?.data;
  if (!Array.isArray(list)) return;
  for (const model of list) {
   if (model?.id) models.set(model.id, model);
  }
  scheduleUpdate();
 }
 function inspectResponse(input, response) {
  if (!response.ok) return;
  let url;
  try {
   const address = typeof input === 'string' || input instanceof URL ? input : input.url;
   url = new URL(address, window.location.href);
  } catch { return; }
  if (url.origin !== window.location.origin) return;
  const chatRoute = /^\/api\/v1\/chats\/[^/]+\/?$/.test(url.pathname);
  const modelRoute = /^\/api\/(?:v1\/)?models\/?$/.test(url.pathname);
  if (!chatRoute && !modelRoute) return;
  response.clone().json().then((payload) => {
   if (chatRoute) cacheChat(payload);
   else cacheModels(payload);
  }).catch(() => {});
 }
 window.fetch = function (input, init) {
  const response = originalFetch.call(this, input, init);
  response.then((result) => inspectResponse(input, result)).catch(() => {});
  return response;
 };
 async function readChat(chatId, fresh = false) {
  if (!chatId || chatId.startsWith('local:') || chatId.startsWith('temporary-')) return null;
  if (pendingReads.has(chatId)) return pendingReads.get(chatId);
  const cached = chats.get(chatId);
  if (cached && (!fresh || Date.now() - (readTimes.get(chatId) ?? 0) < 1000)) return cached;
  let token;
  try { token = localStorage.getItem('token'); } catch { return cached ?? null; }
  if (!token) return cached ?? null;
  const request = originalFetch.call(window, `/api/v1/chats/${encodeURIComponent(chatId)}`, {
   method: 'GET', headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
  }).then(async (response) => {
   if (!response.ok) return cached ?? null;
   const chat = await response.json();
   cacheChat(chat);
   return chats.get(chatId) ?? null;
  }).catch(() => cached ?? null).finally(() => pendingReads.delete(chatId));
  pendingReads.set(chatId, request);
  return request;
 }
 function modelForChat(chat) {
  const selectedName = document.querySelector('#message-input-container button[id^="model-selector-"]')?.textContent?.trim();
  if (chat?.id === currentChatId() && selectedName) {
   for (const model of models.values()) {
    if (model.name === selectedName || model.id === selectedName) return model;
   }
  }
  const selected = chat?.chat?.models;
  const modelId = Array.isArray(selected) ? selected[0] : selected;
  if (modelId) return models.get(modelId) ?? { id: modelId };
  return models.get(runUsage(latestResponse(chat)).modelId) ?? null;
 }
 function modelName(message, fallback) {
  const id = runUsage(message).modelId;
  return models.get(id)?.name ?? message?.modelName ?? id ?? fallback ?? 'Model unavailable';
 }
 function timeText(timestamp, full = false) {
  if (timestamp === null || timestamp === undefined) return 'Time unavailable';
  const date = new Date(Number(timestamp) * 1000);
  if (!Number.isFinite(date.getTime())) return 'Time unavailable';
  if (full) return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
 }
 function exactTokens(value) {
  return value === null ? 'Unavailable' : `${value.toLocaleString()} tokens`;
 }
 function appendRow(list, label, value) {
  const name = document.createElement('dt');
  name.textContent = label;
  const detail = document.createElement('dd');
  detail.textContent = value;
  list.append(name, detail);
 }
 function appendNote(container, value) {
  const note = document.createElement('p');
  note.className = 'owui-usage-note';
  note.textContent = value;
  container.appendChild(note);
 }
 function renderRunDetails(container, message, chat) {
  const usage = runUsage(message);
  const subtitle = document.createElement('p');
  subtitle.className = 'owui-usage-subtitle';
  subtitle.textContent = modelName(message);
  container.appendChild(subtitle);
  const list = document.createElement('dl');
  list.className = 'owui-usage-rows';
  let timeLabel = 'Created';
  if (usage.recorded) timeLabel = 'Started';
  appendRow(list, timeLabel, timeText(usage.startedAt, true));
  if (usage.completedAt !== null) appendRow(list, 'Finished', timeText(usage.completedAt, true));
  appendRow(list, 'Duration', durationText(usage.durationMs));
  appendRow(list, 'Input', exactTokens(usage.input));
  appendRow(list, 'Output', exactTokens(usage.output));
  let totalLabel = 'Saved response total';
  if (usage.recorded) totalLabel = 'Total for this run';
  appendRow(list, totalLabel, exactTokens(usage.total));
  if (usage.status !== 'completed') {
   let status = 'Error';
   if (usage.status === 'cancelled') status = 'Stopped';
   else if (usage.status === 'paused') status = 'Waiting for approval';
   appendRow(list, 'Status', status);
  }
  container.appendChild(list);
  if (!message) appendNote(container, 'No saved response details are available yet.');
  else if (usage.staleAfterError) appendNote(container, 'The latest attempt failed before its run details were saved. Previous-run usage is not attributed to this attempt.');
  else if (!usage.complete) appendNote(container, 'Available provider counts may cover only part of this run.');
  else if (usage.total === null) appendNote(container, 'The provider did not report token usage for this response.');
  else if (usage.recorded) appendNote(container, 'Provider-reported tokens for this run, including reported model and tool steps. Input can include conversation history sent again at each step.');
  else appendNote(container, 'Usage saved with this response. Older responses can include earlier continuations; their individual run boundaries were not recorded.');
  if (panelState?.kind === 'chat' && chat?.chat?.title) appendNote(container, `Chat: ${chat.chat.title}`);
 }
 function renderContextDetails(container, chat) {
  const context = contextInfo(chat, modelForChat(chat));
  const subtitle = document.createElement('p');
  subtitle.className = 'owui-usage-subtitle';
  subtitle.textContent = context.source;
  container.appendChild(subtitle);
  const list = document.createElement('dl');
  list.className = 'owui-usage-rows';
  appendRow(list, 'Context used', exactTokens(context.tokens));
  let limit = 'Limit not reported';
  if (context.capacity) limit = exactTokens(context.capacity.tokens);
  appendRow(list, 'Context capacity', limit);
  if (context.percent !== null) appendRow(list, 'Window used', `${context.percent}%`);
  if (context.threshold > 0) appendRow(list, 'Compaction trigger', exactTokens(context.threshold));
  container.appendChild(list);
  if (context.percent !== null) {
   const meter = document.createElement('div');
   meter.className = 'owui-context-meter';
   meter.setAttribute('role', 'progressbar');
   meter.setAttribute('aria-label', 'Context window used');
   meter.setAttribute('aria-valuemin', '0');
   meter.setAttribute('aria-valuemax', '100');
   meter.setAttribute('aria-valuenow', String(Math.min(context.percent, 100)));
   const fill = document.createElement('span');
   fill.style.width = `${Math.min(context.percent, 100)}%`;
   meter.appendChild(fill);
   container.appendChild(meter);
  }
  if (context.capacity) {
   let origin = 'advertised metadata';
   if (context.capacity.source === 'configured') origin = 'configured settings';
   appendNote(container, `Capacity comes from the model's ${origin}.`);
  } else appendNote(container, 'This model has not supplied a context capacity.');
  if (context.tokens !== null) appendNote(container, 'Context differs from the tokens consumed across a run. A draft, new files, tools, or compaction can change the next request.');
  if (context.source === 'Last request and response' && context.tokens !== null) appendNote(container, 'This value reflects the last measured request and response; it is not a live count of the draft.');
  if (context.threshold > 0) appendNote(container, 'The compaction trigger is a separate chat setting, not the model window size.');
 }
 function renderPanel() {
  if (!panelState) return;
  const { content, chatId, messageId, kind } = panelState;
  const chat = chats.get(chatId);
  const response = messageId ? chat?.chat?.history?.messages?.[messageId] : latestResponse(chat);
  const signature = JSON.stringify([chat?.updated_at, chat?.context_usage, chat?.chat?.params?.num_ctx, response?.meta?.local_run, response?.usage, response?.timestamp, panelState.loading, modelForChat(chat)]);
  if (panelState.signature === signature) return;
  panelState.signature = signature;
  content.replaceChildren();
  if (panelState.loading && !chat) {
   appendNote(content, 'Loading saved details…');
   return;
  }
  if (kind === 'context') renderContextDetails(content, chat);
  else {
   const message = messageId ? chat?.chat?.history?.messages?.[messageId] : latestResponse(chat);
   if (kind === 'chat' && !runUsage(message).recorded) setText(panelState.title, 'Saved response usage');
   renderRunDetails(content, message, chat);
  }
  positionPanel();
 }
 function clearHoverTimers() {
  window.clearTimeout(hoverTimer);
  window.clearTimeout(closeTimer);
  hoverTimer = null;
  closeTimer = null;
 }
 function restorePanelBackground(state) {
  if (state.background) state.background.inert = state.wasInert;
  state.background = null;
  document.documentElement.classList.remove('owui-usage-sheet-open');
 }
 function closePanel(restoreFocus = true) {
  if (!panelState) return;
  const state = panelState;
  panelState = null;
  clearHoverTimers();
  restorePanelBackground(state);
  state.overlay.remove();
  // Restore focus before collapsing a disclosure that may hide its trigger.
  if (restoreFocus && state.trigger?.isConnected) state.trigger.focus({ preventScroll: true });
  state.trigger?.setAttribute('aria-expanded', 'false');
 }
 function visibleBounds(trigger) {
  const viewport = window.visualViewport;
  let left = viewport?.offsetLeft ?? 0;
  let top = viewport?.offsetTop ?? 0;
  let width = viewport?.width ?? window.innerWidth;
  let height = viewport?.height ?? window.innerHeight;
  const segments = window.viewport?.segments ?? viewport?.segments;
  if (segments?.length > 1) {
   const rect = trigger?.getBoundingClientRect();
   const segment = Array.from(segments).find((item) => rect && rect.left >= item.left && rect.left < item.right) ?? segments[0];
   left = Math.max(left, segment.left);
   top = Math.max(top, segment.top);
   width = Math.min(width, segment.width);
   height = Math.min(height, segment.bottom - top);
  }
  return { left: left, top: top, width: width, height: height, segmented: segments?.length > 1 };
 }
 function positionPanel() {
  if (!panelState) return;
  const state = panelState;
  const bounds = visibleBounds(state.trigger);
  const sheet = window.innerWidth < 640 && !bounds.segmented;
  state.overlay.classList.toggle('owui-usage-sheet', sheet);
  state.panel.setAttribute('aria-modal', String(sheet));
  if (sheet) {
   if (!state.background) {
    state.background = document.querySelector('.app');
    state.wasInert = state.background?.inert ?? false;
    if (state.background) state.background.inert = true;
   }
   document.documentElement.classList.add('owui-usage-sheet-open');
   state.panel.style.width = `${bounds.width}px`;
   state.panel.style.maxHeight = `${Math.max(100, bounds.height - 16)}px`;
   state.panel.style.left = `${bounds.left}px`;
   state.panel.style.top = `${bounds.top + bounds.height - Math.min(state.panel.offsetHeight, bounds.height - 16)}px`;
  } else {
   restorePanelBackground(state);
   state.panel.style.width = `${Math.min(360, bounds.width - 24)}px`;
   state.panel.style.maxHeight = `${Math.max(100, bounds.height - 24)}px`;
   const rect = state.trigger?.getBoundingClientRect();
   let left = rect ? rect.right - state.panel.offsetWidth : bounds.left + 12;
   let top = rect ? rect.top - state.panel.offsetHeight - 8 : bounds.top + 12;
   if (top < bounds.top + 12 && rect) top = rect.bottom + 8;
   left = Math.max(bounds.left + 12, Math.min(left, bounds.left + bounds.width - state.panel.offsetWidth - 12));
   top = Math.max(bounds.top + 12, Math.min(top, bounds.top + bounds.height - state.panel.offsetHeight - 12));
   state.panel.style.left = `${left}px`;
   state.panel.style.top = `${top}px`;
  }
 }
 async function openPanel(kind, chatId, messageId, trigger, hover = false) {
  if (panelState?.trigger === trigger && !hover) { closePanel(); return; }
  closePanel(false);
  const overlay = document.createElement('div');
  overlay.className = 'owui-usage-overlay';
  overlay.dataset.owuiUsage = 'true';
  const panel = document.createElement('section');
  panel.className = 'owui-usage-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-labelledby', 'owui-usage-title');
  const header = document.createElement('div');
  header.className = 'owui-usage-header';
  const title = document.createElement('h2');
  title.id = 'owui-usage-title';
  title.textContent = 'Response details';
  if (kind === 'context') title.textContent = 'Context window';
  else if (kind === 'chat') title.textContent = 'Latest run';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'owui-usage-close';
  close.setAttribute('aria-label', 'Close details');
  close.innerHTML = CLOSE_ICON;
  close.addEventListener('click', () => closePanel());
  header.append(title, close);
  const content = document.createElement('div');
  panel.append(header, content);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  const state = { kind: kind, chatId: chatId, messageId: messageId, trigger: trigger, hover: hover, overlay: overlay, panel: panel, content: content, title: title, close: close, loading: true, background: null, wasInert: false };
  panelState = state;
  trigger?.setAttribute('aria-expanded', 'true');
  overlay.addEventListener('pointerdown', (event) => {
   if (event.target === overlay) closePanel();
  });
  panel.addEventListener('pointerenter', () => window.clearTimeout(closeTimer));
  panel.addEventListener('pointerleave', scheduleHoverClose);
  renderPanel();
  positionPanel();
  if (!hover) close.focus({ preventScroll: true });
  await readChat(chatId, true);
  if (panelState !== state) return;
  state.loading = false;
  renderPanel();
 }
 function scheduleHoverClose() {
  if (panelState?.hover) closeTimer = window.setTimeout(() => closePanel(false), 180);
 }
 function bindDetails(button, kind, getIds) {
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', (event) => {
   event.preventDefault();
   event.stopPropagation();
   const ids = getIds();
   openPanel(kind, ids.chatId, ids.messageId, button);
  });
  button.addEventListener('pointerenter', (event) => {
   if (event.pointerType !== 'mouse' || window.matchMedia('(pointer: coarse)').matches) return;
   window.clearTimeout(closeTimer);
   hoverTimer = window.setTimeout(() => {
    const ids = getIds();
    openPanel(kind, ids.chatId, ids.messageId, button, true);
   }, 350);
  });
  button.addEventListener('pointerleave', () => {
   window.clearTimeout(hoverTimer);
   scheduleHoverClose();
  });
 }
 function updateContextBadge(chatId) {
  const composer = document.getElementById('message-input-container');
  if (!composer) { contextBadge = null; return; }
  if (!contextBadge?.isConnected || contextBadge.parentElement?.nextElementSibling !== composer) {
   document.querySelector('.owui-context-rail')?.remove();
   const rail = document.createElement('div');
   rail.className = 'owui-context-rail';
   rail.dataset.owuiUsage = 'true';
   contextBadge = document.createElement('button');
   contextBadge.type = 'button';
   contextBadge.id = 'owui-context-info-button';
   contextBadge.className = 'owui-context-badge';
   contextBadge.innerHTML = `${INFO_ICON}<span></span>`;
   bindDetails(contextBadge, 'context', () => ({ chatId: currentChatId() }));
   rail.appendChild(contextBadge);
   composer.before(rail);
  }
  const chat = chats.get(chatId);
  const context = contextInfo(chat, modelForChat(chat));
  let label = 'Context info';
  if (context.tokens !== null) label = `Context ${compactCount(context.tokens)}`;
  if (context.capacity) label += ` / ${compactCount(context.capacity.tokens)}`;
  if (context.percent !== null) label += ` · ${context.percent}%`;
  setText(contextBadge.querySelector('span'), label);
  contextBadge.setAttribute('aria-label', `${label}. Open context window details`);
 }
 function refreshCompletedChat(chatId) {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => readChat(chatId, true), 500);
 }
 function updateResponseFooters(chatId) {
  const chat = chats.get(chatId);
  for (const assistant of document.querySelectorAll('#messages-container .chat-assistant')) {
   const outer = assistant.closest('[id^="message-"]');
   if (!outer) continue;
   const messageId = outer.id.slice('message-'.length);
   const message = chat?.chat?.history?.messages?.[messageId];
   const nativeInfo = outer.querySelector('button[id^="info-"]');
   const actions = outer.querySelector('.buttons');
   const nativeTime = actions?.querySelector('time');
   const completedKey = `${chatId}/${messageId}`;
   if (!actions || (!nativeTime && !nativeInfo)) {
    knownDone.delete(completedKey);
    outer.querySelector('.owui-run-footer')?.remove();
    outer.classList.remove('owui-run-enhanced');
    continue;
   }
   if (!knownDone.has(completedKey)) {
    knownDone.add(completedKey);
    refreshCompletedChat(chatId);
   }
   let footer = outer.querySelector('.owui-run-footer');
   if (!footer) {
    footer = document.createElement('button');
    footer.type = 'button';
    footer.className = 'owui-run-footer';
    footer.dataset.owuiUsage = 'true';
    footer.innerHTML = '<time></time><span aria-hidden="true">·</span><span class="owui-run-model"></span><span aria-hidden="true">·</span><span class="owui-run-count"></span>';
    bindDetails(footer, 'run', () => ({ chatId: currentChatId(), messageId: messageId }));
    actions.after(footer);
    outer.classList.add('owui-run-enhanced');
   }
   const usage = runUsage(message);
   const fallbackModel = outer.querySelector('#response-message-model-name')?.textContent?.trim();
   const name = modelName(message, fallbackModel);
   let timestamp = usage.startedAt;
   if (timestamp === null) {
    const nativeTime = outer.querySelector('.buttons time')?.getAttribute('datetime');
    if (nativeTime) timestamp = Date.parse(nativeTime) / 1000;
   }
   setText(footer.querySelector('time'), timeText(timestamp));
   setText(footer.querySelector('.owui-run-model'), name);
   let total = 'Usage unavailable';
   if (usage.total !== null) total = `${compactCount(usage.total)} tokens`;
   if (!usage.complete) total += ' (partial)';
   setText(footer.querySelector('.owui-run-count'), total);
   footer.setAttribute('aria-label', `${timeText(timestamp)}, ${name}, ${total}. Open response details`);
  }
 }
 function updateHoverPreviews() {
  for (const messages of document.querySelectorAll('[id^="chat-hover-preview-messages-"]')) {
   const chatId = messages.id.slice('chat-hover-preview-messages-'.length);
   const chat = chats.get(chatId);
   const response = latestResponse(chat);
   if (!response) continue;
   let summary = messages.parentElement.querySelector('.owui-usage-preview');
   if (!summary) {
    summary = document.createElement('div');
    summary.className = 'owui-usage-preview';
    summary.dataset.owuiUsage = 'true';
    summary.innerHTML = '<strong></strong><div></div><div></div>';
    messages.before(summary);
   }
   messages.parentElement.classList.add('owui-usage-preview-panel');
   const usage = runUsage(response);
   let scope = 'Saved response';
   if (usage.recorded) scope = 'Latest recorded run';
   setText(summary.children[0], `${scope} · ${modelName(response)}`);
   let summaryLine = `${timeText(usage.startedAt)} · ${durationText(usage.durationMs)}`;
   if (usage.total !== null) summaryLine += ` · ${compactCount(usage.total)} tokens`;
   else summaryLine += ' · Usage unavailable';
   setText(summary.children[1], summaryLine);
   let split = `Input ${compactCount(usage.input)} · Output ${compactCount(usage.output)}`;
   if (!usage.complete) split += ' · Partial usage';
   setText(summary.children[2], split);
  }
 }
 function updateChatMenu() {
  if (!menuChatId || !menuTrigger?.isConnected) return;
  const expanded = menuTrigger.closest('[aria-haspopup="true"]');
  if (expanded?.getAttribute('aria-expanded') !== 'true') return;
  const menu = document.querySelector('body > [role="menu"] .app-dropdown-menu');
  if (!menu || menu.querySelector('.owui-chat-details-action')) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'owui-chat-details-action';
  button.setAttribute('role', 'menuitem');
  button.innerHTML = `${INFO_ICON}<span>Chat details</span>`;
  const chatId = menuChatId;
  const trigger = menuTrigger;
  button.addEventListener('click', (event) => {
   event.preventDefault();
   event.stopPropagation();
   window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
   window.setTimeout(() => openPanel('chat', chatId, null, trigger), 0);
  });
  menu.prepend(button);
 }
 function update() {
  scheduledFrame = null;
  const chatId = currentChatId();
  if (chatId !== activeId) {
   activeId = chatId;
   knownDone.clear();
   closePanel(false);
   if (chatId && !chats.has(chatId)) readChat(chatId);
  }
  updateContextBadge(chatId);
  updateResponseFooters(chatId);
  updateHoverPreviews();
  updateChatMenu();
 }
 function scheduleUpdate() {
  if (scheduledFrame !== null) return;
  scheduledFrame = window.requestAnimationFrame(update);
 }
 function captureSidebarMenu(event) {
  const menu = event.target.closest?.('#sidebar-chat-item-menu');
  if (!menu) return;
  const group = menu.closest('#sidebar-chat-group');
  const anchor = group?.querySelector('a[href^="/c/"]');
  if (!anchor) return;
  menuChatId = decodeURIComponent(new URL(anchor.href).pathname.split('/').pop());
  menuTrigger = menu.querySelector('button') ?? event.target;
  scheduleUpdate();
 }
 function handleDocumentKeydown(event) {
  if (!panelState) return;
  if (event.key === 'Escape') {
   event.preventDefault();
   event.stopPropagation();
   closePanel();
   return;
  }
  if (event.key === 'Tab' && panelState.overlay.classList.contains('owui-usage-sheet')) {
   event.preventDefault();
   panelState.close.focus({ preventScroll: true });
  }
 }
 function handleOutsidePointer(event) {
  if (!panelState) return;
  if (panelState.panel.contains(event.target) || panelState.trigger?.contains(event.target)) return;
  closePanel(false);
 }
 function handleMutations(records) {
  const changed = records.some((record) => !record.target.closest?.('[data-owui-usage="true"]'));
  if (changed) scheduleUpdate();
 }
 function start() {
  const style = document.createElement('style');
  style.id = 'owui-chat-usage-style';
  style.textContent = css;
  document.head.appendChild(style);
  document.addEventListener('click', captureSidebarMenu, true);
  document.addEventListener('keydown', captureSidebarMenu, true);
  document.addEventListener('keydown', handleDocumentKeydown);
  document.addEventListener('pointerdown', handleOutsidePointer, true);
  window.addEventListener('popstate', scheduleUpdate);
  window.addEventListener('pageshow', scheduleUpdate);
  window.addEventListener('resize', positionPanel);
  window.visualViewport?.addEventListener('resize', positionPanel);
  window.visualViewport?.addEventListener('scroll', positionPanel);
  const observer = new MutationObserver(handleMutations);
  observer.observe(document.body, { childList: true, subtree: true });
  update();
 }
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
 else start();
})();
