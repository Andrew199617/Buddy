/* A readable chat header with native navigation, controls, and Settings. */
(() => {
 'use strict';
 if (window.__owuiChatHeader) return;
 window.__owuiChatHeader = true;
 const root = document.documentElement;
 const css = `
#chat-container nav.owui-chat-header {
 margin-bottom:0 !important; padding:4px 8px !important; min-height:44px; flex-shrink:0;
 background:#fff; border-bottom:1px solid rgba(148,163,184,.14); isolation:isolate;
}
#chat-container nav.owui-chat-header > div:first-child { padding-inline:0 !important; }
.owui-chat-header #navbar-bg-gradient-to-b { display:none !important; }
.owui-chat-header .owui-header-row { min-width:0; gap:4px; flex-wrap:nowrap; }
.owui-chat-header .owui-header-leading { flex-shrink:0; margin:0 !important; }
.owui-chat-header .owui-header-title-wrap { flex:1 1 0%; min-width:0; margin:0 !important; padding:0 !important; overflow:hidden; }
.owui-chat-header .owui-header-title-row { min-width:0; margin:0 !important; gap:4px !important; }
.owui-chat-header .owui-header-title { flex:1 1 0%; min-width:0; margin:0; padding-block:0 !important; font-size:.9375rem; line-height:1.5; font-weight:500; color:#334155; }
.owui-chat-header .owui-header-controls { flex-shrink:0; gap:4px !important; margin:0 !important; }
#chat-container nav.owui-chat-header .owui-header-control {
 display:inline-flex !important; align-items:center; justify-content:center; flex-shrink:0;
 width:36px !important; height:36px !important; min-width:36px !important; min-height:36px !important;
 padding:0 !important; border-radius:10px !important; color:#64748b; cursor:pointer; touch-action:manipulation;
}
#chat-container nav.owui-chat-header .owui-header-control > div { padding:0 !important; }
#chat-container nav.owui-chat-header .owui-header-control svg { width:21px !important; height:21px !important; }
#chat-container nav.owui-chat-header .owui-header-control:hover { background:rgba(100,116,139,.08); color:#0f172a; }
#chat-container nav.owui-chat-header .owui-header-control:focus-visible { outline:2px solid #0284c7; outline-offset:2px; }
#chat-container:has(nav.owui-chat-header) #messages-container > .h-full > .h-full { padding-top:16px !important; }
.dark #chat-container nav.owui-chat-header { background:#171717; border-bottom-color:rgba(255,255,255,.07); }
.dark .owui-chat-header .owui-header-title { color:#e5e7eb; }
.dark #chat-container nav.owui-chat-header .owui-header-control { color:#d1d5db; }
.dark #chat-container nav.owui-chat-header .owui-header-control:hover { background:rgba(255,255,255,.08); color:#fff; }
@supports (backdrop-filter:blur(16px)) or (-webkit-backdrop-filter:blur(16px)) {
 #chat-container nav.owui-chat-header { background:rgba(255,255,255,.84); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); }
 .dark #chat-container nav.owui-chat-header { background:rgba(23,23,23,.86); }
}
@media (prefers-reduced-transparency:reduce) {
 #chat-container nav.owui-chat-header { background:#fff; backdrop-filter:none; -webkit-backdrop-filter:none; }
 .dark #chat-container nav.owui-chat-header { background:#171717; }
}
@media (max-width:767px), (pointer:coarse) {
 #chat-container nav.owui-chat-header { min-height:52px; }
 .owui-chat-header .owui-header-title { font-size:1rem; }
 #chat-container nav.owui-chat-header .owui-header-control { width:44px !important; height:44px !important; min-width:44px !important; min-height:44px !important; border-radius:12px !important; }
 #chat-container nav.owui-chat-header .owui-header-control svg { width:24px !important; height:24px !important; }
}
`;
 const SETTINGS_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M10.343 3.94c.09-.542.56-.94 1.11-.94h1.093c.55 0 1.02.398 1.11.94l.149.894c.07.424.384.764.78.93.398.164.855.142 1.205-.108l.737-.527a1.125 1.125 0 011.45.12l.773.774c.39.389.44 1.002.12 1.45l-.527.737c-.25.35-.272.806-.107 1.204.165.397.505.71.93.78l.893.15c.543.09.94.56.94 1.109v1.094c0 .55-.397 1.02-.94 1.11l-.893.149c-.425.07-.765.383-.93.78-.165.398-.143.854.107 1.204l.527.738c.32.447.269 1.06-.12 1.45l-.774.773a1.125 1.125 0 01-1.449.12l-.738-.527c-.35-.25-.806-.272-1.203-.107-.397.165-.71.505-.781.929l-.149.894c-.09.542-.56.94-1.11.94h-1.094c-.55 0-1.019-.398-1.11-.94l-.148-.894c-.071-.424-.384-.764-.781-.93-.398-.164-.854-.142-1.204.108l-.738.527c-.447.32-1.06.269-1.45-.12l-.773-.774a1.125 1.125 0 01-.12-1.45l.527-.737c.25-.35.273-.806.108-1.204-.165-.397-.505-.71-.93-.78l-.894-.15c-.542-.09-.94-.56-.94-1.109v-1.094c0-.55.398-1.02.94-1.11l.894-.149c.424-.07.765-.383.93-.78.165-.398.143-.854-.107-1.204l-.527-.738a1.125 1.125 0 01.12-1.45l.773-.773a1.125 1.125 0 011.45-.12l.737.527c.35.25.807.272 1.204.107.397-.165.71-.505.78-.929l.15-.894z"/><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>';
 let currentHeader = null;
 let headerObserver = null;
 let pendingFrame = null;

 function settingsAddress() {
  const url = new URL(window.location.href);
  url.searchParams.set('settings', 'general');
  return `${url.pathname}${url.search}${url.hash}`;
 }
 function ensureSettingsControl(controls) {
  let link = controls.querySelector('#owui-chat-settings-button');
  if (!link) {
   link = document.createElement('a');
   link.id = 'owui-chat-settings-button';
   link.className = 'owui-header-control';
   link.setAttribute('aria-label', 'Settings');
   link.setAttribute('title', 'Settings');
   link.setAttribute('data-sveltekit-noscroll', '');
   link.innerHTML = SETTINGS_ICON;
   controls.appendChild(link);
  }
  // SvelteKit's native same-route link opens Settings and removes the query.
  const address = settingsAddress();
  if (link.getAttribute('href') !== address) link.setAttribute('href', address);
  return link;
 }
 function reportHeaderHeight() {
  if (!currentHeader?.isConnected) return;
  const height = `${Math.ceil(currentHeader.getBoundingClientRect().height)}px`;
  if (root.style.getPropertyValue('--owui-chat-header-height') !== height) root.style.setProperty('--owui-chat-header-height', height);
 }
 function watchHeader(header) {
  if (header === currentHeader) return;
  headerObserver?.disconnect();
  currentHeader = header;
  if (!header) {
   root.style.removeProperty('--owui-chat-header-height');
   return;
  }
  headerObserver = new ResizeObserver(reportHeaderHeight);
  headerObserver.observe(header);
 }
 function update() {
  pendingFrame = null;
  const header = document.querySelector('#chat-container nav.drag-region');
  if (!header) { watchHeader(null); return; }
  const titleWrap = header.querySelector('div.flex-1.overflow-hidden');
  const titleRow = titleWrap?.firstElementChild;
  const controls = titleWrap?.nextElementSibling;
  const row = titleWrap?.parentElement;
  if (!row || !controls || !titleRow) return;
  watchHeader(header);
  header.classList.add('owui-chat-header');
  row.classList.add('owui-header-row');
  titleWrap.classList.add('owui-header-title-wrap');
  titleRow.classList.add('owui-header-title-row');
  const title = titleRow.firstElementChild;
  if (title) {
   title.classList.add('owui-header-title');
   const text = title.textContent.trim();
   if (title.getAttribute('title') !== text) title.setAttribute('title', text);
  }
  titleWrap.previousElementSibling?.classList.add('owui-header-leading');
  controls.classList.add('owui-header-controls');
  ensureSettingsControl(controls);
  for (const button of header.querySelectorAll('button')) {
   if (button.classList.contains('hidden')) button.classList.remove('owui-header-control');
   else button.classList.add('owui-header-control');
  }
  reportHeaderHeight();
 }
 function scheduleUpdate() {
  if (pendingFrame === null) pendingFrame = requestAnimationFrame(update);
 }
 function start() {
  const style = document.createElement('style');
  style.id = 'owui-chat-header-style';
  style.textContent = css;
  document.head.appendChild(style);
  const observer = new MutationObserver(scheduleUpdate);
  observer.observe(document.body, { childList:true, characterData:true, subtree:true });
  window.addEventListener('resize', scheduleUpdate);
  window.addEventListener('popstate', scheduleUpdate);
  window.addEventListener('pageshow', scheduleUpdate);
  window.visualViewport?.addEventListener('resize', scheduleUpdate);
  update();
 }
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
 else start();
})();
