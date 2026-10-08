import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, mockChat } from './mobile-ui-preview.mjs';

const outputDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const chatPath = '/c/mobile-preview-chat-1';
const baseline = process.argv.includes('--baseline');
const results = [];

function longFixture(options = {}) {
  const chat = mockChat('mobile-preview-chat-1', options);
  const reply = chat.chat.history.messages['preview-assistant-message'];
  const paragraphs = [];
  for (let index = 1; index <= 24; index += 1) {
    paragraphs.push(`Paragraph ${index}. A useful mobile conversation keeps all response text readable. You should be able to move to the latest answer and continue reading with enough space above the composer. The keyboard and a longer draft should resize the visible conversation without hiding its last lines.`);
  }
  paragraphs.push('LAST RESPONSE LINE: This conclusion and the response details below it should remain fully visible above the composer.');
  reply.content = paragraphs.join('\n\n');
  return chat;
}

async function createLongPage(options) {
  const session = await createMockedPage({ ...options, urlPath: chatPath });
  if (options.appleRenderBranch) {
    await session.context.addInitScript(() => {
      Object.defineProperty(navigator, 'vendor', { configurable: true, get: () => 'Apple Computer, Inc.' });
    });
  }
  await session.context.route('**/api/v1/chats/mobile-preview-chat-1', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(longFixture(options)) });
  });
  await session.page.reload({ waitUntil: 'domcontentloaded' });
  await session.page.locator('#message-preview-assistant-message .markdown-prose').waitFor({ state: 'attached' });
  await session.page.locator('#chat-input').waitFor({ state: 'visible' });
  await session.page.locator('.owui-run-footer').waitFor({ state: 'attached' });
  await session.page.evaluate(() => document.fonts.ready);
  await session.page.waitForTimeout(400);
  return session;
}

async function metrics(page) {
  return page.evaluate(() => {
    function geometry(element) {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        bottom: rect.bottom, clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight, scrollTop: element.scrollTop,
        heightCss: style.height, minHeight: style.minHeight,
        overflow: style.overflow, flexShrink: style.flexShrink,
        paddingTop: style.paddingTop, paddingBottom: style.paddingBottom, lineHeight: style.lineHeight
      };
    }
    const messages = document.querySelector('#messages-container');
    const reply = document.querySelector('#message-preview-assistant-message');
    const paragraphs = reply?.querySelectorAll('.markdown-prose p');
    return {
      viewport: { height: visualViewport.height, offsetTop: visualViewport.offsetTop },
      navbar: geometry(document.querySelector('#navbar-bg-gradient-to-b')?.closest('nav')),
      pane: geometry(document.querySelector('#chat-pane')),
      messages: geometry(messages),
      wrapper: geometry(messages?.firstElementChild),
      messagesComponent: geometry(messages?.firstElementChild?.firstElementChild),
      reply: geometry(reply),
      lastLine: geometry(paragraphs?.[paragraphs.length - 1]),
      footer: geometry(reply?.querySelector('.owui-run-footer')),
      composer: geometry(document.querySelector('#message-input-container')),
      scrollButton: geometry(document.querySelector('button[aria-label="Scroll to bottom"]')),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth
    };
  });
}

async function nativeBottom(page) {
  await page.locator('#messages-container').evaluate((element) => {
    element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight - 400);
  });
  await page.getByRole('button', { name: 'Scroll to bottom', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Scroll to bottom', exact: true }).click();
  await page.waitForTimeout(250);
}

function assertBottom(value, mobile = true) {
  assert.ok(value.lastLine.bottom <= value.messages.bottom - 8, 'Last text line should stay inside the conversation viewport');
  const readableTop = Math.max(value.messages.y, value.navbar?.bottom ?? value.messages.y);
  assert.ok(value.lastLine.bottom >= readableTop + parseFloat(value.lastLine.lineHeight) - 1, 'At least the final full text line should remain visible below the header');
  assert.ok(value.footer.y >= readableTop, 'Response details must remain fully visible below the header');
  assert.ok(value.footer.bottom <= value.messages.bottom - 8, 'Response details should have space below them');
  assert.ok(value.composer.y - value.footer.bottom >= 16, 'Response details need a comfortable gap above the composer');
  assert.ok(value.pane.scrollHeight <= value.pane.clientHeight + 1, 'Conversation should have a single scroll container');
  assert.ok(value.messages.scrollHeight - value.messages.scrollTop - value.messages.clientHeight <= 2);
  assert.equal(value.horizontalOverflow, false);
}

async function scenario(name, options) {
  const session = await createLongPage(options);
  const { page } = session;
  try {
    const initial = await metrics(page);
    await nativeBottom(page);
    const bottom = await metrics(page);
    await page.screenshot({ path: resolve(outputDirectory, `mobile-scroll-${name}-bottom.png`) });
    if (!baseline) assertBottom(bottom, options.hasTouch !== false);
    const record = { test: name, initial, bottom };
    if (options.hasTouch !== false) {
      await page.locator('#message-preview-assistant-message .markdown-prose p').last().evaluate((paragraph) => {
        paragraph.textContent += ' More response text arrived while the view was at the bottom. The latest lines should remain pinned through this additional content.';
      });
      await page.waitForTimeout(180);
      record.growing = await metrics(page);
      if (!baseline) assertBottom(record.growing);
    }
    if (options.hasTouch !== false) {
      await page.evaluate(() => {
        Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
        Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
        visualViewport.dispatchEvent(new Event('resize'));
      });
      await page.waitForTimeout(150);
      const keyboard = await metrics(page);
      await page.locator('#chat-input').fill('Draft line one\nDraft line two\nDraft line three');
      await page.waitForTimeout(150);
      const draft = await metrics(page);
      await page.screenshot({ path: resolve(outputDirectory, `mobile-scroll-${name}-keyboard-draft.png`) });
      if (!baseline) {
        assertBottom(keyboard);
        assertBottom(draft);
        assert.ok(draft.composer.bottom <= 402 + 1);
      }
      await page.locator('#chat-input').fill('Draft line one\nDraft line two\nDraft line three\nDraft line four\nDraft line five\nDraft line six');
      await page.waitForTimeout(150);
      record.extremeDraft = await metrics(page);
      await page.screenshot({ path: resolve(outputDirectory, `mobile-scroll-${name}-six-line-draft.png`) });
      if (!baseline) assertBottom(record.extremeDraft);
      await page.locator('#chat-input').fill('Draft line one\nDraft line two\nDraft line three');
      await page.waitForTimeout(100);
      await page.locator('#messages-container').evaluate((element) => { element.scrollTop = 300; });
      await page.waitForTimeout(100);
      const earlier = await metrics(page);
      await page.evaluate(() => {
        Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 480 });
        visualViewport.dispatchEvent(new Event('resize'));
      });
      await page.waitForTimeout(150);
      const earlierResized = await metrics(page);
      if (!baseline) assert.ok(Math.abs(earlier.messages.scrollTop - earlierResized.messages.scrollTop) <= 1, 'Reading older text should preserve the scroll position through resize');
      await page.locator('#message-preview-assistant-message .markdown-prose p').last().evaluate((paragraph) => {
        paragraph.textContent += ' This arrived while the reader was reviewing an earlier part of the conversation.';
      });
      await page.waitForTimeout(180);
      const earlierGrowing = await metrics(page);
      if (!baseline) assert.ok(Math.abs(earlierResized.messages.scrollTop - earlierGrowing.messages.scrollTop) <= 1, 'New output should not pull a reader away from older text');
      Object.assign(record, { keyboard, draft, earlier, earlierResized, earlierGrowing });
    }
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    results.push(record);
    console.log(JSON.stringify({
      test: name,
      bottom: { scrollTop: bottom.messages.scrollTop, scrollHeight: bottom.messages.scrollHeight, height: bottom.messages.clientHeight, footerGap: bottom.composer.y - bottom.footer.bottom },
      keyboard: record.keyboard ? { height: record.keyboard.messages.clientHeight, lastLineBottom: record.keyboard.lastLine.bottom, footerGap: record.keyboard.composer.y - record.keyboard.footer.bottom } : null,
      draft: record.draft ? { height: record.draft.messages.clientHeight, navbarBottom: record.draft.navbar?.bottom, lastLineBottom: record.draft.lastLine.bottom, footerGap: record.draft.composer.y - record.draft.footer.bottom } : null,
      extremeDraft: record.extremeDraft ? { height: record.extremeDraft.messages.clientHeight, navbarBottom: record.extremeDraft.navbar?.bottom, lastLineBottom: record.extremeDraft.lastLine.bottom, footerGap: record.extremeDraft.composer.y - record.extremeDraft.footer.bottom } : null
    }, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function partialFooterScenario() {
  const session = await createLongPage({ viewport: { width: 320, height: 740 }, textScale: 1.25, usageFixture: 'partial' });
  try {
    const { page } = session;
    await page.evaluate(() => {
      Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
      Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
      visualViewport.dispatchEvent(new Event('resize'));
    });
    await page.waitForTimeout(150);
    const footer = page.locator('.owui-run-footer');
    const value = await footer.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const count = element.querySelector('.owui-run-count');
      return {
        x: rect.x, right: rect.right, height: rect.height,
        ariaLabel: element.getAttribute('aria-label'),
        count: count.textContent,
        countOverflow: getComputedStyle(count).textOverflow,
        countWidth: count.getBoundingClientRect().width,
        countScrollWidth: count.scrollWidth,
        documentOverflow: document.documentElement.scrollWidth > innerWidth
      };
    });
    assert.ok(value.x >= 0 && value.right <= 320);
    assert.equal(value.documentOverflow, false);
    assert.match(value.ariaLabel, /Usage unavailable \(partial\)/);
    assert.equal(value.count, 'Usage unavailable (partial)');
    assert.equal(value.countOverflow, 'ellipsis');
    assert.ok(value.countWidth < value.countScrollWidth, 'Long count labels should use ellipsis within the narrow row');
    await page.screenshot({ path: resolve(outputDirectory, 'mobile-scroll-partial-footer.png') });
    results.push({ test: 'narrow-partial-footer', ...value });
    console.log(JSON.stringify({ test: 'narrow-partial-footer', ...value }, null, 2));
  } finally {
    await session.browser.close();
  }
}

try {
  await scenario('large-text', { viewport: { width: 320, height: 740 }, textScale: 1.25 });
  await scenario('phone', { viewport: { width: 390, height: 844 } });
  await scenario('apple-render-branch', { viewport: { width: 390, height: 844 }, appleRenderBranch: true });
  await scenario('desktop', { viewport: { width: 1280, height: 900 }, hasTouch: false, isMobile: false });
  await partialFooterScenario();
  writeFileSync(resolve(outputDirectory, `mobile-scroll-${baseline ? 'baseline' : 'result'}.json`), JSON.stringify(results, null, 2));
} catch (error) {
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
