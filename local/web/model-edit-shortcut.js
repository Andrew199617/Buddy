/* Direct pen shortcut beside native Workspace model menus. */
(() => {
 'use strict';
 if (window.__owuiModelEditShortcut) return;
 window.__owuiModelEditShortcut = true;
 const css = `
#model-list .owui-model-edit-actions { gap:6px; }
#model-list .owui-model-edit-shortcut, #model-list .owui-model-edit-menu {
 display:inline-flex; align-items:center; justify-content:center; flex-shrink:0;
 width:32px; height:32px; min-width:32px; min-height:32px; padding:0;
 border-radius:10px; cursor:pointer; touch-action:manipulation; color:#64748b;
}
#model-list .owui-model-edit-shortcut { background:rgba(100,116,139,.06); border:1px solid rgba(100,116,139,.09); }
#model-list .owui-model-edit-shortcut svg, #model-list .owui-model-edit-menu svg { width:18px; height:18px; }
#model-list .owui-model-edit-menu > div { width:auto; height:auto; color:inherit; }
#model-list .owui-model-edit-shortcut:hover, #model-list .owui-model-edit-menu:hover { color:#0f172a; background:rgba(100,116,139,.12); }
#model-list .owui-model-edit-shortcut:focus-visible, #model-list .owui-model-edit-menu:focus-visible { outline:2px solid #0284c7; outline-offset:2px; }
.dark #model-list .owui-model-edit-shortcut, .dark #model-list .owui-model-edit-menu { color:#d1d5db; }
.dark #model-list .owui-model-edit-shortcut { background:rgba(255,255,255,.045); border-color:rgba(255,255,255,.07); }
.dark #model-list .owui-model-edit-shortcut:hover, .dark #model-list .owui-model-edit-menu:hover { color:#fff; background:rgba(255,255,255,.1); }
@media (max-width:767px), (pointer:coarse) {
 #model-list .owui-model-edit-shortcut, #model-list .owui-model-edit-menu { width:44px; height:44px; min-width:44px; min-height:44px; border-radius:12px; }
 #model-list .owui-model-edit-shortcut svg, #model-list .owui-model-edit-menu svg { width:20px; height:20px; }
}
@media (max-width:767px) {
 #model-list .owui-model-edit-row .owui-model-edit-title-line { flex-wrap:wrap; row-gap:0; }
 #model-list .owui-model-edit-row .owui-model-edit-title-wrap { flex:1 1 100%; }
}
@media (max-width:359px) {
 #model-list .owui-model-edit-row:has(.owui-model-edit-shortcut) { flex-wrap:wrap; row-gap:4px; }
 #model-list .owui-model-edit-row:has(.owui-model-edit-shortcut) > div:last-child { flex-basis:100%; margin-left:0; justify-content:flex-end; }
}
`;
 const PEN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125"/></svg>';
 let pendingFrame = null;

 function canEdit(row) {
  return row.getAttribute('tabindex') === '0';
 }
 function modelTitle(row) {
  return row.querySelector('a[href^="/?model="]')?.textContent.trim() || 'model';
 }
 function createEditButton(row) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'owui-model-edit-shortcut';
  button.innerHTML = PEN_ICON;
  function openNativeEditor(event) {
   event.preventDefault();
   event.stopPropagation();
   // The row owns the native permission check and correctly encoded editor navigation.
   if (row.isConnected && canEdit(row)) row.click();
  }
  button.addEventListener('click', openNativeEditor);
  return button;
 }
 function clearRow(row) {
  row.querySelector('.owui-model-edit-shortcut')?.remove();
  row.classList.remove('owui-model-edit-row');
  for (const element of row.querySelectorAll('.owui-model-edit-actions, .owui-model-edit-menu, .owui-model-edit-title-line, .owui-model-edit-title-wrap')) {
   element.classList.remove('owui-model-edit-actions', 'owui-model-edit-menu', 'owui-model-edit-title-line', 'owui-model-edit-title-wrap');
  }
 }
 function decorateTitle(row) {
  const link = row.querySelector('a[href^="/?model="]');
  const titleWrapper = link?.parentElement;
  const titleLine = titleWrapper?.parentElement;
  if (!titleWrapper || !titleLine) return;
  titleWrapper.classList.add('owui-model-edit-title-wrap');
  titleLine.classList.add('owui-model-edit-title-line');
 }
 function updateRow(row) {
  const menu = row.querySelector('span[role="button"][aria-haspopup="true"][aria-expanded]');
  const menuButton = menu?.querySelector('button');
  if (!menu || !menuButton) {
   clearRow(row);
   return;
  }
  row.classList.add('owui-model-edit-row');
  menu.parentElement.classList.add('owui-model-edit-actions');
  menuButton.classList.add('owui-model-edit-menu');
  decorateTitle(row);
  let edit = row.querySelector('.owui-model-edit-shortcut');
  if (!canEdit(row)) {
   edit?.remove();
   return;
  }
  if (!edit) edit = createEditButton(row);
  if (edit.nextElementSibling !== menu) menu.parentElement.insertBefore(edit, menu);
  const label = `Edit ${modelTitle(row)}`;
  if (edit.getAttribute('aria-label') !== label) edit.setAttribute('aria-label', label);
  if (edit.getAttribute('title') !== label) edit.setAttribute('title', label);
 }
 function update() {
  pendingFrame = null;
  const list = document.getElementById('model-list');
  if (!list) return;
  for (const row of list.querySelectorAll('[id^="model-item-"][role="button"]')) updateRow(row);
 }
 function schedule() {
  if (pendingFrame === null) pendingFrame = requestAnimationFrame(update);
 }
 function start() {
  const style = document.createElement('style');
  style.id = 'owui-model-edit-shortcut-style';
  style.textContent = css;
  document.head.appendChild(style);
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:['tabindex'] });
  schedule();
 }
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
 else start();
})();
