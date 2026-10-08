/* Phone Settings navigation built around Open WebUI's existing tabs and panels. */
(() => {
 'use strict';
 if (window.__owuiMobileSettings) return;
 window.__owuiMobileSettings = true;

 const css = `
.owui-mobile-settings {
 position:fixed !important; inset:auto !important; top:var(--owui-settings-top) !important;
 left:var(--owui-settings-left) !important; width:var(--owui-settings-width) !important;
 height:var(--owui-settings-height) !important; padding:0 !important; align-items:stretch !important;
 justify-content:stretch !important; overflow:hidden !important; scrollbar-gutter:auto !important; background:transparent !important;
}
.owui-mobile-settings .owui-settings-panel {
 --owui-settings-bg:#f7f7f8; --owui-settings-card:#fff; --owui-settings-line:rgba(0,0,0,.07);
 --owui-settings-text:#202124; --owui-settings-muted:#6b7280;
 width:100% !important; max-width:none !important; height:100% !important; max-height:none !important;
 min-height:0 !important; margin:0 !important; border:0 !important; border-radius:0 !important;
 box-shadow:none !important; transform:none !important; display:flex !important; flex-direction:column !important;
 background:var(--owui-settings-bg) !important; color:var(--owui-settings-text); overflow:hidden !important;
 padding-top:env(safe-area-inset-top,0px); font-size:1rem;
}
.dark .owui-mobile-settings .owui-settings-panel {
 --owui-settings-bg:#171717; --owui-settings-card:#262626; --owui-settings-line:rgba(255,255,255,.08);
 --owui-settings-text:#f3f4f6; --owui-settings-muted:#a3a3a3;
}
.owui-settings-header {
 display:flex; flex-shrink:0; align-items:center; gap:12px; padding:12px 16px 14px;
 min-height:76px; background:var(--owui-settings-bg); z-index:1;
}
.owui-settings-heading { flex:1; min-width:0; margin:0; font-size:1.875rem; line-height:1.2; font-weight:650; letter-spacing:-.025em; overflow-wrap:anywhere; }
.owui-settings-heading:focus { outline:none; }
.owui-settings-header .owui-settings-close, .owui-settings-back {
 display:inline-flex !important; align-items:center; justify-content:center; flex-shrink:0;
 width:44px !important; height:44px !important; min-width:44px; min-height:44px; padding:0 !important;
 margin:0 !important; border-radius:50% !important; background:var(--owui-settings-card); color:var(--owui-settings-text) !important;
 cursor:pointer; touch-action:manipulation;
}
.owui-settings-header .owui-settings-close > :not(.owui-settings-close-icon) { display:none !important; }
.owui-settings-header .owui-settings-close-icon { width:24px; height:24px; }
.owui-settings-back svg { width:24px; height:24px; }
.owui-settings-panel[data-settings-view="home"] .owui-settings-back { display:none !important; }
.owui-settings-panel[data-settings-view="detail"] .owui-settings-header { min-height:68px; padding-block:10px; gap:10px; border-bottom:1px solid var(--owui-settings-line); }
.owui-settings-panel[data-settings-view="detail"] .owui-settings-heading { font-size:1.1875rem; font-weight:600; letter-spacing:0; }
.owui-settings-panel[data-settings-view="detail"] .owui-settings-back { background:transparent; }
.owui-settings-panel[data-settings-view="detail"] #settings-tabs-container { display:none !important; }
.owui-settings-panel[data-settings-view="home"] .owui-settings-content { display:none !important; }
.owui-mobile-settings #settings-tabs-container {
 display:flex; flex:1 1 0%; min-height:0 !important; width:100% !important; border:0 !important;
 padding:0 16px max(16px,env(safe-area-inset-bottom,0px)); overflow:hidden;
}
.owui-settings-search {
 min-height:44px !important; height:44px !important; margin:0 0 4px !important; padding:0 12px !important;
 border-radius:14px !important; gap:8px !important; background:var(--owui-settings-card) !important;
 color:var(--owui-settings-muted);
}
.owui-settings-search svg { width:20px !important; height:20px !important; }
.owui-settings-search input { min-width:0; height:100%; font-size:max(16px,1rem) !important; color:var(--owui-settings-text) !important; }
.owui-mobile-settings #settings-tabs-container > .tabs {
 display:flex !important; flex-direction:column !important; max-height:none !important; min-height:0;
 overflow-x:hidden !important; overflow-y:auto !important; overscroll-behavior:contain;
 padding:0 0 8px !important; gap:0 !important; scrollbar-width:thin;
}
.owui-settings-group { display:block !important; margin:18px 12px 8px !important; padding:0 !important; font-size:.8125rem !important; font-weight:500; line-height:1.4; color:var(--owui-settings-muted) !important; }
.owui-settings-scope { display:none !important; }
.owui-mobile-settings #settings-tabs-container .owui-settings-category-row {
 position:relative; display:flex !important; align-items:center !important; flex-shrink:0; gap:12px !important;
 width:100% !important; min-height:56px !important; height:auto !important; margin:0 !important;
 padding:14px !important; border:0 !important; border-radius:0 !important;
 background:var(--owui-settings-card) !important; color:var(--owui-settings-text) !important;
 font-size:1rem !important; font-weight:400 !important; line-height:1.45 !important; text-align:left;
 white-space:normal !important; cursor:pointer; touch-action:manipulation;
}
.owui-settings-category-row > svg:not(.owui-settings-chevron) { width:22px !important; height:22px !important; flex-shrink:0; }
.owui-settings-category-row > span { flex:1; min-width:0; overflow-wrap:anywhere; }
.owui-settings-category-row .owui-settings-chevron { width:20px; height:20px; flex-shrink:0; margin-left:auto; color:var(--owui-settings-muted); }
.owui-mobile-settings #settings-tabs-container .owui-settings-group-first { border-top-left-radius:18px !important; border-top-right-radius:18px !important; }
.owui-mobile-settings #settings-tabs-container .owui-settings-group-last { border-bottom-left-radius:18px !important; border-bottom-right-radius:18px !important; }
.owui-settings-category-row:not(.owui-settings-group-last)::after { content:""; position:absolute; left:14px; right:14px; bottom:0; height:1px; background:var(--owui-settings-line); }
.owui-mobile-settings button:focus-visible { outline:2px solid #0284c7; outline-offset:-2px; }
.owui-mobile-settings .owui-settings-category-row:active { filter:brightness(.95); }
.owui-mobile-settings .owui-settings-content { flex:1 1 0%; min-height:0 !important; min-width:0; padding:16px 16px max(12px,env(safe-area-inset-bottom,0px)) !important; }
.owui-settings-content .owui-settings-category { min-height:0; }
.owui-settings-redundant-heading, .owui-settings-heading-wrapper { display:none !important; }
.owui-settings-content .text-xs { font-size:.9375rem !important; line-height:1.5 !important; }
.owui-settings-content .text-sm { font-size:1rem !important; line-height:1.5 !important; }
.owui-settings-content [class*="text-[0.6875rem]"], .owui-settings-content [class*="text-[0.625rem]"] { font-size:.8125rem !important; line-height:1.5 !important; color:var(--owui-settings-muted); }
.owui-settings-content section { margin-top:16px !important; padding:16px; background:var(--owui-settings-card); border-radius:18px; }
.owui-settings-content section:first-of-type { margin-top:0 !important; }
.owui-settings-content section > h3 { margin-bottom:14px !important; font-size:.875rem !important; font-weight:600; color:var(--owui-settings-text); }
.owui-settings-content input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"]), .owui-settings-content select {
 min-height:44px !important; font-size:max(16px,1rem) !important; max-width:100%;
}
.owui-settings-content textarea { font-size:max(16px,1rem) !important; min-height:96px; }
.owui-settings-content button:not([role="switch"]):not([role="checkbox"]) { min-height:40px; }
.owui-settings-content .shrink-0.justify-end > button { min-height:44px; padding-inline:20px; font-size:1rem; border-radius:22px; }
.owui-settings-content .overflow-y-auto { overscroll-behavior:contain; scrollbar-width:thin; }
@media (max-width:359px) {
 .owui-settings-content section .flex.items-center.justify-between:has(select) { flex-direction:column; align-items:stretch !important; gap:8px !important; }
 .owui-settings-content section .flex.items-center.justify-between:has(select) > div:last-child { width:100%; }
 .owui-settings-content section .flex.items-center.justify-between:has(select) > div:last-child > .inline-flex { width:100% !important; }
}
`;
 const SVG_NS = 'http://www.w3.org/2000/svg';
 let current = null;
 let pendingFrame = null;

 function makeIcon(path, className) {
  const icon = document.createElementNS(SVG_NS, 'svg');
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('fill', 'none');
  icon.setAttribute('stroke', 'currentColor');
  icon.setAttribute('stroke-width', '1.8');
  icon.setAttribute('aria-hidden', 'true');
  icon.classList.add(className);
  const shape = document.createElementNS(SVG_NS, 'path');
  shape.setAttribute('d', path);
  shape.setAttribute('stroke-linecap', 'round');
  shape.setAttribute('stroke-linejoin', 'round');
  icon.appendChild(shape);
  return icon;
 }
 function selectedCategory(state) {
  return state.nav.querySelector('button[role="tab"][aria-selected="true"]');
 }
 function categoryLabel(button) {
  return button?.querySelector('span')?.textContent.trim() || 'Settings';
 }
 function setAttributeIfChanged(element, name, value) {
  if (element.getAttribute(name) !== value) element.setAttribute(name, value);
 }
 function savedStyle(element, name) {
  return { value:element.style.getPropertyValue(name), priority:element.style.getPropertyPriority(name) };
 }
 function restoreStyle(element, name, original) {
  if (original.value) element.style.setProperty(name, original.value, original.priority);
  else element.style.removeProperty(name);
 }
 function restoreAttribute(element, name, value) {
  if (value === null) element.removeAttribute(name);
  else element.setAttribute(name, value);
 }
 function setView(state, view, focus) {
  state.view = view;
  setAttributeIfChanged(state.panel, 'data-settings-view', view);
  updateHeading(state);
  if (!focus) return;
  requestAnimationFrame(() => {
   if (current !== state || !state.modal.isConnected) return;
   if (view === 'detail') {
    state.heading.focus({ preventScroll:true });
    return;
   }
   const selected = selectedCategory(state);
   if (selected) {
    selected.focus({ preventScroll:true });
    selected.scrollIntoView({ block:'nearest' });
   } else state.search.focus({ preventScroll:true });
  });
 }
 function updateHeading(state) {
  let title = 'Settings';
  if (state.view === 'detail') title = categoryLabel(selectedCategory(state));
  if (state.heading.textContent !== title) state.heading.textContent = title;
 }
 function updateViewport(state) {
  const viewport = window.visualViewport;
  const useVisibleViewport = viewport && viewport.scale === 1;
  let top = 0;
  let left = 0;
  let width = window.innerWidth;
  let height = window.innerHeight;
  if (useVisibleViewport) {
   top = viewport.offsetTop;
   left = viewport.offsetLeft;
   width = viewport.width;
   height = viewport.height;
  }
  state.modal.style.setProperty('--owui-settings-top', `${top}px`);
  state.modal.style.setProperty('--owui-settings-left', `${left}px`);
  state.modal.style.setProperty('--owui-settings-width', `${width}px`);
  state.modal.style.setProperty('--owui-settings-height', `${height}px`);
 }
 function decorateCategoryRows(state) {
  let group = [];
  function finishGroup() {
   if (!group.length) return;
   group[0].classList.add('owui-settings-group-first');
   group[group.length - 1].classList.add('owui-settings-group-last');
   group = [];
  }
  for (const child of state.tabs.children) {
   child.classList.remove('owui-settings-group-first', 'owui-settings-group-last');
   if (child.matches('button[role="tab"]')) {
    child.classList.add('owui-settings-category-row');
    if (!child.querySelector('.owui-settings-chevron')) child.appendChild(makeIcon('m9 5 7 7-7 7', 'owui-settings-chevron'));
    group.push(child);
   } else {
    finishGroup();
    if (child.tagName === 'SPAN') child.classList.add('owui-settings-group');
   }
  }
  finishGroup();
  const firstScope = state.tabs.firstElementChild;
  if (firstScope?.tagName === 'SPAN') firstScope.classList.add('owui-settings-scope');
 }
 function decorateCategoryPanel(state) {
  const category = state.content.firstElementChild?.firstElementChild;
  if (!category) return;
  category.classList.add('owui-settings-category');
  const heading = category.querySelector('h2');
  if (!heading) return;
  heading.classList.remove('owui-settings-redundant-heading');
  const wrapper = heading.parentElement;
  wrapper.classList.remove('owui-settings-heading-wrapper');
  // Keep subsection titles and headings with counts; the page header replaces an exact duplicate only.
  if (heading.textContent.trim() !== categoryLabel(selectedCategory(state))) return;
  heading.classList.add('owui-settings-redundant-heading');
  if (wrapper !== category && wrapper.childElementCount === 1) wrapper.classList.add('owui-settings-heading-wrapper');
 }
 function createState(nav) {
  const panel = nav.parentElement;
  const modal = panel.closest('.modal[role="dialog"]');
  const tabs = nav.querySelector('.tabs');
  const search = nav.querySelector('[data-settings-search]');
  const close = nav.querySelector(':scope > button');
  const content = nav.nextElementSibling;
  if (!modal || !tabs || !search || !close || !content) return null;

  const header = document.createElement('header');
  header.className = 'owui-settings-header';
  const back = document.createElement('button');
  back.type = 'button';
  back.className = 'owui-settings-back';
  back.setAttribute('aria-label', 'Back to Settings');
  back.appendChild(makeIcon('m15 5-7 7 7 7', 'owui-settings-back-icon'));
  const heading = document.createElement('h1');
  heading.id = 'owui-mobile-settings-heading';
  heading.className = 'owui-settings-heading';
  heading.tabIndex = -1;
  const closeIcon = makeIcon('m6 6 12 12M18 6 6 18', 'owui-settings-close-icon');
  const state = {
   nav, panel, modal, tabs, search, close, content, header, back, heading, closeIcon,
   searchWrapper:search.parentElement, closeNext:close.nextSibling,
   originalCloseLabel:close.getAttribute('aria-label'), originalModalLabel:modal.getAttribute('aria-labelledby'),
   originalTabsOrientation:tabs.getAttribute('aria-orientation'), originalView:panel.getAttribute('data-settings-view'),
   originalPanelWidth:savedStyle(panel, 'width'), originalPanelMaxWidth:savedStyle(panel, 'max-width'),
   view:'home'
  };
  back.addEventListener('click', () => setView(state, 'home', true));
  close.classList.add('owui-settings-close');
  close.setAttribute('aria-label', 'Close Settings');
  close.appendChild(closeIcon);
  header.appendChild(back);
  header.appendChild(heading);
  header.appendChild(close);
  panel.prepend(header);
  modal.classList.add('owui-mobile-settings');
  panel.classList.add('owui-settings-panel');
  // Native important width utilities outrank ordinary extension styles. Restore these on desktop.
  panel.style.setProperty('width', '100%', 'important');
  panel.style.setProperty('max-width', 'none', 'important');
  content.classList.add('owui-settings-content');
  state.searchWrapper.classList.add('owui-settings-search');
  tabs.setAttribute('aria-orientation', 'vertical');
  modal.setAttribute('aria-labelledby', heading.id);
  const selected = selectedCategory(state);
  if (selected && selected.getAttribute('aria-controls') !== 'tab-general') state.view = 'detail';
  setView(state, state.view, false);
  return state;
 }
 function disposeState(state) {
  state.closeIcon.remove();
  state.close.classList.remove('owui-settings-close');
  restoreAttribute(state.close, 'aria-label', state.originalCloseLabel);
  if (state.nav.isConnected) state.nav.insertBefore(state.close, state.closeNext);
  state.header.remove();
  state.modal.classList.remove('owui-mobile-settings');
  state.panel.classList.remove('owui-settings-panel');
  restoreStyle(state.panel, 'width', state.originalPanelWidth);
  restoreStyle(state.panel, 'max-width', state.originalPanelMaxWidth);
  state.content.classList.remove('owui-settings-content');
  state.searchWrapper.classList.remove('owui-settings-search');
  restoreAttribute(state.modal, 'aria-labelledby', state.originalModalLabel);
  restoreAttribute(state.tabs, 'aria-orientation', state.originalTabsOrientation);
  restoreAttribute(state.panel, 'data-settings-view', state.originalView);
  state.modal.style.removeProperty('--owui-settings-top');
  state.modal.style.removeProperty('--owui-settings-left');
  state.modal.style.removeProperty('--owui-settings-width');
  state.modal.style.removeProperty('--owui-settings-height');
  for (const child of state.tabs.children) child.classList.remove('owui-settings-category-row', 'owui-settings-group-first', 'owui-settings-group-last', 'owui-settings-group', 'owui-settings-scope');
  for (const icon of state.tabs.querySelectorAll('.owui-settings-chevron')) icon.remove();
  for (const element of state.content.querySelectorAll('.owui-settings-category, .owui-settings-redundant-heading, .owui-settings-heading-wrapper')) element.classList.remove('owui-settings-category', 'owui-settings-redundant-heading', 'owui-settings-heading-wrapper');
 }
 function update() {
  pendingFrame = null;
  const nav = document.getElementById('settings-tabs-container');
  if (!nav && !current) return;
  const phone = window.innerWidth < 768;
  if (current && (!phone || nav !== current.nav || !current.modal.isConnected)) {
   disposeState(current);
   current = null;
  }
  if (!phone || !nav) return;
  if (!current) current = createState(nav);
  if (!current) return;
  updateViewport(current);
  decorateCategoryRows(current);
  decorateCategoryPanel(current);
  updateHeading(current);
 }
 function schedule() {
  if (pendingFrame === null) pendingFrame = requestAnimationFrame(update);
 }
 function handleCategoryClick(event) {
  if (!current || !(event.target instanceof Element)) return;
  const button = event.target.closest('button[role="tab"]');
  if (button && current.nav.contains(button)) setView(current, 'detail', true);
 }
 function dismissNativeMenu(state) {
  const menus = document.querySelectorAll('[role="menu"], .app-dropdown-menu');
  const visibleMenu = Array.from(menus).some((menu) => menu.getClientRects().length > 0);
  if (!visibleMenu) return false;
  const expanded = state.content.querySelector('[aria-expanded="true"]');
  let trigger = expanded;
  if (expanded && !expanded.matches('button')) trigger = expanded.querySelector('button') || expanded;
  // Reuse the native outside-dismiss callbacks; a shared Escape would close both menu and Settings.
  state.heading.dispatchEvent(new PointerEvent('pointerdown', { bubbles:true, button:0 }));
  state.heading.dispatchEvent(new MouseEvent('click', { bubbles:true, button:0 }));
  requestAnimationFrame(() => {
   if (current !== state) return;
   if (trigger?.isConnected) trigger.focus({ preventScroll:true });
   else state.heading.focus({ preventScroll:true });
  });
  return true;
 }
 function handleKeys(event) {
  const state = current;
  if (!state || !(event.target instanceof Element)) return;
  if (event.key === 'Enter' && event.target === state.search) {
   const hasMatches = state.tabs.querySelector('button[role="tab"]');
   if (hasMatches) requestAnimationFrame(() => { if (current === state) setView(state, 'detail', true); });
   return;
  }
  if (event.key !== 'Escape') return;
  const modals = document.querySelectorAll('.modal');
  if (modals[modals.length - 1] !== state.modal) return;
  if (event.target === state.search && state.search.value) {
   event.preventDefault();
   event.stopImmediatePropagation();
   // Let the existing input binding clear the filter before the modal or focus trap sees Escape.
   state.search.value = '';
   state.search.dispatchEvent(new Event('input', { bubbles:true }));
   return;
  }
  if (state.view !== 'detail') return;
  if (dismissNativeMenu(state)) {
   event.preventDefault();
   event.stopImmediatePropagation();
   return;
  }
  event.preventDefault();
  event.stopImmediatePropagation();
  setView(state, 'home', true);
 }
 function start() {
  const style = document.createElement('style');
  style.id = 'owui-mobile-settings-style';
  style.textContent = css;
  document.head.appendChild(style);
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:['aria-selected'] });
  document.addEventListener('click', handleCategoryClick, true);
  window.addEventListener('keydown', handleKeys, true);
  window.addEventListener('resize', schedule);
  window.visualViewport?.addEventListener('resize', schedule);
  window.visualViewport?.addEventListener('scroll', schedule);
  schedule();
 }
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
 else start();
})();
