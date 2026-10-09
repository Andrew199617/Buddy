/* Make the native pending-response cursor visible and understandable. */
(function () {
 'use strict';

 function activityLabel(initialText, currentText, thinking) {
  if (thinking) return 'Thinking…';
  if (currentText !== initialText) return 'Working…';
  return 'Waiting for response…';
 }

 function elapsedText(startedAt, now) {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  if (seconds === 0) return '';
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
 }

 if (typeof module !== 'undefined' && module.exports) {
  module.exports = { activityLabel, elapsedText };
 }
 if (typeof window === 'undefined' || window.__owuiModelActivity) return;
 window.__owuiModelActivity = true;

 const css = `
.owui-model-activity { display:flex; align-items:center; gap:8px; min-height:28px; margin:5px 0; color:#4b5563; font-size: calc(.875rem + var(--buddy-font-size-offset, 0px)); line-height:1.5; }
.owui-model-activity-spinner { width:16px; height:16px; flex-shrink:0; border:2px solid rgba(107,114,128,.22); border-top-color:#0284c7; border-radius:50%; animation:owui-model-activity-spin .9s linear infinite; }
.owui-model-activity-elapsed { color:#6b7280; font-size: calc(.8125rem + var(--buddy-font-size-offset, 0px)); font-variant-numeric:tabular-nums; white-space:nowrap; }
.owui-model-activity-elapsed:not(:empty)::before { content:'·'; margin-right:8px; }
.owui-model-activity-cursor { display:none !important; }
.dark .owui-model-activity { color:#d1d5db; }
.dark .owui-model-activity-spinner { border-color:rgba(156,163,175,.25); border-top-color:#38bdf8; }
.dark .owui-model-activity-elapsed { color:#9ca3af; }
@keyframes owui-model-activity-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .owui-model-activity-spinner { animation:none; border-color:#0284c7; } }
`;
 const activities = new Map();
 let scheduledFrame = null;
 let timer = null;

 function nativeCursor(container) {
  const spans = container.querySelectorAll(':scope > div > span.animate-pulse.align-text-bottom');
  for (const span of spans) {
   if (span.classList.contains('w-[0.125rem]')) return span;
  }
  return null;
 }

 function responseText(container, cursor, indicator) {
  const parts = [];
  for (const child of container.children) {
   if (child === indicator || child === cursor.parentElement) continue;
   parts.push(child.textContent || '');
  }
  return parts.join('\n');
 }

 function isThinking(container) {
  for (const element of container.querySelectorAll('.shimmer')) {
   if (/^thinking(?:\s|\.|…|$)/i.test(element.textContent.trim())) return true;
  }
  return false;
 }

 function createActivity(container, cursor, outer) {
  const indicator = document.createElement('div');
  indicator.className = 'owui-model-activity';
  indicator.setAttribute('role', 'status');
  indicator.setAttribute('aria-live', 'polite');
  indicator.setAttribute('aria-atomic', 'false');
  const spinner = document.createElement('span');
  spinner.className = 'owui-model-activity-spinner';
  spinner.setAttribute('aria-hidden', 'true');
  const label = document.createElement('span');
  const elapsed = document.createElement('span');
  elapsed.className = 'owui-model-activity-elapsed';
  elapsed.setAttribute('aria-hidden', 'true');
  indicator.append(spinner, label, elapsed);
  const state = {
   container, cursor, outer, indicator, label, elapsed,
   startedAt: performance.now(),
   initialText: responseText(container, cursor, indicator)
  };
  cursor.parentElement.classList.add('owui-model-activity-cursor');
  container.appendChild(indicator);
  activities.set(container, state);
  return state;
 }

 function removeActivity(state) {
  state.indicator.remove();
  state.cursor.parentElement?.classList.remove('owui-model-activity-cursor');
  activities.delete(state.container);
 }

 function updateElapsed() {
  const now = performance.now();
  for (const state of activities.values()) {
   const text = elapsedText(state.startedAt, now);
   if (state.elapsed.textContent !== text) state.elapsed.textContent = text;
  }
 }

 function synchronizeTimer() {
  if (activities.size && timer === null) timer = window.setInterval(updateElapsed, 1000);
  if (!activities.size && timer !== null) {
   window.clearInterval(timer);
   timer = null;
  }
 }

 function update() {
  scheduledFrame = null;
  const activeContainers = new Set();
  const containers = document.querySelectorAll('#messages-container [id="response-content-container"]');
  for (const container of containers) {
   const outer = container.closest('[id^="message-"]');
   const cursor = nativeCursor(container);
   const completed = outer?.querySelector('.buttons .copy-response-button');
   if (!outer || !cursor || completed) continue;
   activeContainers.add(container);
   let state = activities.get(container);
   if (state && state.cursor !== cursor) {
    removeActivity(state);
    state = null;
   }
   if (!state) state = createActivity(container, cursor, outer);
   const text = responseText(container, cursor, state.indicator);
   const label = activityLabel(state.initialText, text, isThinking(container));
   if (state.label.textContent !== label) state.label.textContent = label;
  }
  for (const state of activities.values()) {
   if (!activeContainers.has(state.container)) removeActivity(state);
  }
  synchronizeTimer();
  updateElapsed();
 }

 function scheduleUpdate() {
  if (scheduledFrame !== null) return;
  scheduledFrame = window.requestAnimationFrame(update);
 }

 function handleMutations(records) {
  for (const record of records) {
   const element = record.target.nodeType === Node.ELEMENT_NODE
    ? record.target
    : record.target.parentElement;
   if (!element?.closest('.owui-model-activity')) {
    scheduleUpdate();
    return;
   }
  }
 }

 function start() {
  const style = document.createElement('style');
  style.id = 'owui-model-activity-style';
  style.textContent = css;
  document.head.appendChild(style);
  const observer = new MutationObserver(handleMutations);
  observer.observe(document.body, { childList:true, subtree:true, characterData:true });
  window.addEventListener('popstate', scheduleUpdate);
  window.addEventListener('pageshow', scheduleUpdate);
  update();
 }

 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
 else start();
})();
