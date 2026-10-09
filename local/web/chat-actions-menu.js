/* Scoped, touch-friendly presentation of the native chat actions menus. */
(() => {
 'use strict';
 if (window.__owuiChatActionsMenu) return;
 window.__owuiChatActionsMenu = true;

 const css = `
html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button {
 width:32px !important; height:32px !important; flex-shrink:0; border-radius:10px !important;
 border:1px solid #e5e7eb; background:#f8fafc; color:#64748b;
}
html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button:hover, html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button:focus-visible { background:#eef2f6; color:#111827; }
html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button svg { width:20px !important; height:20px !important; }
html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button:focus-visible { outline:2px solid #0284c7; outline-offset:3px; }
.owui-chat-menu-portal {
 position:fixed !important; left:var(--owui-chat-menu-left) !important; top:var(--owui-chat-menu-top) !important;
 right:auto !important; bottom:auto !important; width:var(--owui-chat-menu-width) !important;
 max-height:var(--owui-chat-menu-height) !important; overflow-y:auto !important; overscroll-behavior:contain;
 border-radius:20px; scrollbar-width:thin; z-index:9999;
}
.owui-chat-actions-panel {
 box-sizing:border-box; width:100% !important; min-width:0 !important; max-width:none !important;
 padding:8px !important; border-radius:20px !important; border:1px solid #e2e8f0 !important;
 background:#fff !important; color:#1f2937 !important;
 box-shadow:0 16px 48px rgba(15,23,42,.14),0 3px 12px rgba(15,23,42,.08) !important;
}
.owui-chat-actions-heading { padding:10px 12px 12px; margin:0 0 6px; border-bottom:1px solid #e5e7eb; }
.owui-chat-actions-heading h2 { margin:0; font-size: calc(1rem + var(--buddy-font-size-offset, 0px)); line-height:1.5; font-weight:600; letter-spacing:-.01em; }
.owui-chat-actions-heading p { margin:3px 0 0; font-size: calc(.8125rem + var(--buddy-font-size-offset, 0px)); line-height:1.5; color:#64748b; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.owui-chat-actions-panel .owui-chat-action {
 display:flex !important; align-items:center !important; gap:12px !important; width:100% !important;
 min-height:38px !important; height:auto !important; padding:9px 12px !important; border-radius:12px !important;
 font-size: calc(.9375rem + var(--buddy-font-size-offset, 0px)) !important; font-weight:400; line-height:1.35 !important; text-align:start; color:inherit;
 white-space:normal; transition:background-color .12s;
}
.owui-chat-actions-panel .owui-chat-action:hover { background:#f1f5f9 !important; }
.owui-chat-actions-panel .owui-chat-action:focus-visible { background:#eef6ff !important; outline:2px solid #0284c7; outline-offset:-2px; }
.owui-chat-actions-panel .owui-chat-action > svg, .owui-chat-actions-panel .owui-chat-action > div > svg {
 width:18px !important; height:18px !important; flex-shrink:0; color:#64748b;
}
.owui-chat-actions-panel .owui-chat-action > .owui-chat-submenu-arrow { width:16px !important; height:16px !important; margin-inline-start:auto; color:#94a3b8; }
.owui-chat-actions-panel .owui-chat-action.owui-chat-delete { color:#dc2626 !important; }
.owui-chat-actions-panel .owui-chat-delete svg { color:inherit !important; }
.owui-chat-actions-panel .owui-chat-delete:hover, .owui-chat-actions-panel .owui-chat-delete:focus-visible { background:#fef2f2 !important; }
.owui-chat-actions-panel > hr { margin:7px 12px !important; border-color:#e5e7eb !important; }
.owui-chat-actions-panel .owui-chat-tags { max-height:150px !important; padding:7px 12px 5px !important; }
.owui-chat-actions-panel .owui-chat-tags input { font-size: calc(1rem + var(--buddy-font-size-offset, 0px)) !important; min-width:0; }
.owui-chat-submenu-portal {
 position:fixed !important; left:var(--owui-chat-menu-left) !important; top:var(--owui-chat-menu-top) !important;
 right:auto !important; bottom:auto !important; padding:0 !important; width:var(--owui-chat-menu-width) !important;
 max-height:var(--owui-chat-menu-height) !important; overflow-y:auto !important; overscroll-behavior:contain;
 border-radius:20px; z-index:10001 !important;
}
html.dark:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button { background:#242424; border-color:#383838; color:#d1d5db; }
html.dark:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button:hover, html.dark:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button:focus-visible { background:#303030; color:#fff; }
.dark .owui-chat-actions-panel { background:#242424 !important; color:#f3f4f6 !important; border-color:#3b3b3b !important; box-shadow:0 18px 54px rgba(0,0,0,.38) !important; }
.dark .owui-chat-actions-heading, .dark .owui-chat-actions-panel > hr { border-color:#3b3b3b !important; }
.dark .owui-chat-actions-heading p, .dark .owui-chat-actions-panel .owui-chat-action > svg, .dark .owui-chat-actions-panel .owui-chat-action > div > svg { color:#a3a3a3; }
.dark .owui-chat-actions-panel .owui-chat-action:hover { background:#343434 !important; }
.dark .owui-chat-actions-panel .owui-chat-action:focus-visible { background:#303a46 !important; }
.dark .owui-chat-actions-panel .owui-chat-delete { color:#f87171 !important; }
.dark .owui-chat-actions-panel .owui-chat-delete:hover, .dark .owui-chat-actions-panel .owui-chat-delete:focus-visible { background:#3d2828 !important; }
@media (max-width:767px), (pointer:coarse) {
 html:not(:has(.buddy-chat, .buddy-shell)) #chat-context-menu-button { width:40px !important; height:40px !important; border-radius:12px !important; }
 .owui-chat-actions-panel .owui-chat-action { min-height:46px !important; padding:12px !important; font-size: calc(1rem + var(--buddy-font-size-offset, 0px)) !important; }
 .owui-chat-actions-panel .owui-chat-action > svg, .owui-chat-actions-panel .owui-chat-action > div > svg { width:20px !important; height:20px !important; }
 .owui-chat-actions-panel .owui-chat-tags button { min-height:36px; }
}
@media (prefers-reduced-motion:reduce) { .owui-chat-actions-panel .owui-chat-action { transition:none; } }
`;
 const CHEVRON = '<svg class="owui-chat-submenu-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';
 const menus = new Map();
 let pendingOwner = null;
 let pendingSubmenu = null;
 let scheduledFrame = null;
 let styleElement = null;

 function hasBuddyLayout() {
  return Boolean(document.querySelector('.buddy-chat, .buddy-shell'));
 }
 function clearLegacyMenus() {
  for (const state of menus.values()) state.resizeObserver.disconnect();
  menus.clear();
  pendingOwner = null;
  pendingSubmenu = null;
 }
 function isTouchLayout() {
  return window.innerWidth < 768 || window.matchMedia('(pointer:coarse)').matches;
 }
 function menuPortals() {
  return Array.from(document.querySelectorAll('body > [role="menu"]'));
 }
 function triggerWrapper(trigger) {
  return trigger?.closest('[aria-haspopup="true"][aria-expanded]');
 }
 function ownerIsOpen(owner) {
  return owner?.trigger?.isConnected && triggerWrapper(owner.trigger)?.getAttribute('aria-expanded') === 'true';
 }
 function currentTitle(trigger) {
  const group = trigger?.closest('#sidebar-chat-group');
  const row = group?.querySelector('a[href^="/c/"]');
  if (row) return row.getAttribute('title') || row.textContent.trim();
  const title = trigger?.closest('nav')?.querySelector('.truncate');
  return title?.textContent?.trim() || 'Current conversation';
 }
 function captureOwner(event) {
  if (hasBuddyLayout() || !(event.target instanceof Element)) return;
  if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
  const headerTrigger = event.target.closest('#chat-context-menu-button');
  const sidebarTrigger = event.target.closest('#sidebar-chat-item-menu')?.querySelector('button');
  const trigger = headerTrigger || sidebarTrigger;
  if (!trigger) return;
  pendingOwner = { trigger, title:currentTitle(trigger), previousPortals:new Set(menuPortals()) };
  scheduleUpdate();
 }
 function captureSubmenu(event) {
  if (hasBuddyLayout() || !(event.target instanceof Element)) return;
  if (event.type === 'keydown' && !['Enter', ' ', 'ArrowRight'].includes(event.key)) return;
  const button = event.target.closest('.owui-chat-submenu-trigger');
  if (!button) return;
  if (pendingSubmenu?.trigger === button) {
   scheduleUpdate();
   return;
  }
  const parent = button.closest('.owui-chat-menu-portal');
  const owner = menus.get(parent);
  if (!owner) return;
  pendingSubmenu = { trigger:button, parent, title:button.textContent.trim(), previousPortals:new Set(menuPortals()) };
  scheduleUpdate();
 }
 function isDeleteAction(button) {
  return Array.from(button.querySelectorAll('svg path')).some((path) => {
   const drawing = path.getAttribute('d') || '';
   return drawing.includes('M14.74 9l-.346') || drawing.includes('M20 9L18.005');
  });
 }
 function decorateActions(panel) {
  for (const child of Array.from(panel.children)) {
   let button = null;
   if (child.tagName === 'BUTTON') button = child;
   else if (child.tagName === 'DIV' && child.firstElementChild?.tagName === 'BUTTON' && child.children.length === 1) {
    button = child.firstElementChild;
    button.classList.add('owui-chat-submenu-trigger');
    if (!button.querySelector('.owui-chat-submenu-arrow')) button.insertAdjacentHTML('beforeend', CHEVRON);
   }
   if (!button) {
    if (child.querySelector('input')) child.classList.add('owui-chat-tags');
    continue;
   }
   button.classList.add('owui-chat-action');
   button.setAttribute('role', 'menuitem');
   if (isDeleteAction(button)) button.classList.add('owui-chat-delete');
  }
 }
 function decorateMenu(portal, owner, submenu = false) {
  const panel = portal.querySelector('.app-dropdown-menu');
  if (!panel) return;
  const state = { ...owner, portal, panel, submenu, openedUrl:window.location.href, returnFocus:false };
  menus.set(portal, state);
  portal.classList.add(submenu ? 'owui-chat-submenu-portal' : 'owui-chat-menu-portal');
  panel.classList.remove('rounded-xl!', 'p-0.5!', '[&>button]:px-2!', '[&>a]:px-2!', '[&>button]:rounded-xl!', '[&>a]:rounded-xl!');
  panel.classList.add('owui-chat-actions-panel');
  portal.setAttribute('aria-label', submenu ? owner.title : 'Chat actions');
  if (!submenu) {
   const heading = document.createElement('div');
   heading.className = 'owui-chat-actions-heading';
   const label = document.createElement('h2');
   label.textContent = 'Chat actions';
   const title = document.createElement('p');
   title.textContent = owner.title;
   title.title = owner.title;
   heading.append(label, title);
   panel.prepend(heading);
   state.heading = heading;
  }
  decorateActions(panel);
  // Keep nested native actions inside their parent's outside-click boundary.
  if (submenu && owner.parent?.isConnected) owner.parent.appendChild(portal);
  state.resizeObserver = new ResizeObserver(scheduleUpdate);
  state.resizeObserver.observe(panel);
 }
 function visibleBounds(trigger) {
  const viewport = window.visualViewport;
  let left = viewport?.offsetLeft ?? 0;
  let top = viewport?.offsetTop ?? 0;
  let right = left + (viewport?.width ?? window.innerWidth);
  let bottom = top + (viewport?.height ?? window.innerHeight);
  const segments = window.viewport?.segments || viewport?.segments;
  if (segments?.length > 1) {
   const rect = trigger.getBoundingClientRect();
   const centerX = rect.left + rect.width / 2;
   const centerY = rect.top + rect.height / 2;
   const segment = Array.from(segments).find((part) => centerX >= part.left && centerX <= part.right && centerY >= part.top && centerY <= part.bottom);
   if (segment) {
    left = Math.max(left, segment.left);
    top = Math.max(top, segment.top);
    right = Math.min(right, segment.right);
    bottom = Math.min(bottom, segment.bottom);
   }
  }
  return { left, top, right, bottom };
 }
 function setVariable(element, name, value) {
  if (element.style.getPropertyValue(name) !== value) element.style.setProperty(name, value);
 }
 function positionMenu(state) {
  if (!state.trigger?.isConnected) return;
  const pad = 12;
  const bounds = visibleBounds(state.trigger);
  const availableWidth = Math.max(0, bounds.right - bounds.left - pad * 2);
  const preferredWidth = isTouchLayout() ? 280 : 260;
  const width = Math.min(preferredWidth, availableWidth);
  const maxHeight = Math.max(0, bounds.bottom - bounds.top - pad * 2);
  const rect = state.trigger.getBoundingClientRect();
  const height = Math.min(state.panel.scrollHeight, maxHeight);
  let left = rect.left;
  let top = rect.bottom + 8;
  if (state.submenu) {
   left = rect.right + 8;
   if (left + width > bounds.right - pad) left = rect.left - width - 8;
   top = rect.top;
   // A phone shows the submenu as a readable inset panel over its parent.
   if (left < bounds.left + pad) left = rect.left + 12;
  } else if (top + height > bounds.bottom - pad && rect.top - bounds.top > bounds.bottom - rect.bottom) {
   top = rect.top - height - 8;
  }
  left = Math.max(bounds.left + pad, Math.min(left, bounds.right - pad - width));
  top = Math.max(bounds.top + pad, Math.min(top, bounds.bottom - pad - height));
  setVariable(state.portal, '--owui-chat-menu-left', `${left}px`);
  setVariable(state.portal, '--owui-chat-menu-top', `${top}px`);
  setVariable(state.portal, '--owui-chat-menu-width', `${width}px`);
  setVariable(state.portal, '--owui-chat-menu-height', `${maxHeight}px`);
 }
 function findUnownedPortal(previousPortals) {
  return menuPortals().find((portal) => !menus.has(portal) && !previousPortals.has(portal) && portal.querySelector('.app-dropdown-menu'));
 }
 function update() {
  scheduledFrame = null;
  const buddyLayout = hasBuddyLayout();
  if (styleElement) styleElement.disabled = buddyLayout;
  if (buddyLayout) {
   // Native Buddy menus retain their own positioning, appearance, and keyboard behavior.
   clearLegacyMenus();
   return;
  }
  for (const [portal, state] of menus) {
   if (!portal.isConnected) {
    state.resizeObserver.disconnect();
    menus.delete(portal);
    if (state.returnFocus && state.trigger?.isConnected && state.openedUrl === window.location.href) state.trigger.focus({ preventScroll:true });
    continue;
   }
   if (state.heading && state.panel.firstElementChild !== state.heading) state.panel.prepend(state.heading);
   decorateActions(state.panel);
   positionMenu(state);
  }
  if (pendingOwner && !ownerIsOpen(pendingOwner)) pendingOwner = null;
  const parentOwner = menus.get(pendingSubmenu?.parent);
  if (pendingSubmenu && (!pendingSubmenu.parent.isConnected || !ownerIsOpen(parentOwner))) pendingSubmenu = null;
  if (pendingOwner && ownerIsOpen(pendingOwner)) {
   const portal = findUnownedPortal(pendingOwner.previousPortals);
   if (portal) {
    decorateMenu(portal, pendingOwner);
    positionMenu(menus.get(portal));
    pendingOwner = null;
   }
  }
  if (pendingSubmenu?.parent?.isConnected) {
   const portal = findUnownedPortal(pendingSubmenu.previousPortals);
   if (portal) {
    decorateMenu(portal, pendingSubmenu, true);
    positionMenu(menus.get(portal));
    pendingSubmenu = null;
   }
  }
  // The header menu has a stable native action ID, including when already open.
  const headerPortal = document.querySelector('body > [role="menu"] #chat-copy-button')?.closest('[role="menu"]');
  const trigger = document.getElementById('chat-context-menu-button');
  if (headerPortal && trigger && !menus.has(headerPortal)) {
   decorateMenu(headerPortal, { trigger, title:currentTitle(trigger) });
   positionMenu(menus.get(headerPortal));
  }
 }
 function scheduleUpdate() {
  if (scheduledFrame === null) scheduledFrame = requestAnimationFrame(update);
 }
 function navigateMenu(event) {
  if (hasBuddyLayout() || !(event.target instanceof Element)) return;
  const portal = event.target.closest('.owui-chat-submenu-portal, .owui-chat-menu-portal');
  if (!portal) return;
  const state = menus.get(portal);
  if (!state) return;
  if (event.key === 'Escape') {
   if (!state.submenu) state.returnFocus = true;
   return;
  }
  if (event.target.closest('input, textarea')) return;
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const actions = Array.from(state.panel.querySelectorAll('.owui-chat-action')).filter((button) => !button.disabled && button.getClientRects().length > 0 && button.closest('.owui-chat-submenu-portal, .owui-chat-menu-portal') === portal);
  if (!actions.length) return;
  const active = actions.indexOf(document.activeElement);
  let next = 0;
  if (event.key === 'End') next = actions.length - 1;
  else if (event.key === 'ArrowDown') next = (active + 1) % actions.length;
  else if (event.key === 'ArrowUp') next = (active - 1 + actions.length) % actions.length;
  event.preventDefault();
  actions[next].focus({ preventScroll:true });
  actions[next].scrollIntoView({ block:'nearest', inline:'nearest' });
 }
 function start() {
  styleElement = document.createElement('style');
  styleElement.id = 'owui-chat-actions-style';
  styleElement.textContent = css;
  styleElement.disabled = hasBuddyLayout();
  document.head.appendChild(styleElement);
  document.addEventListener('click', captureOwner, true);
  document.addEventListener('keydown', captureOwner, true);
  document.addEventListener('click', captureSubmenu, true);
  document.addEventListener('mouseover', captureSubmenu, true);
  document.addEventListener('keydown', captureSubmenu, true);
  document.addEventListener('keydown', navigateMenu);
  window.addEventListener('resize', scheduleUpdate);
  window.addEventListener('scroll', scheduleUpdate, true);
  window.visualViewport?.addEventListener('resize', scheduleUpdate);
  window.visualViewport?.addEventListener('scroll', scheduleUpdate);
  const observer = new MutationObserver(scheduleUpdate);
  observer.observe(document.body, { childList:true, subtree:true });
  update();
 }
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
 else start();
})();
