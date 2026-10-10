import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { chromium, webkit } from 'playwright';

export const outputDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const workspace = resolve(outputDirectory, '../../..');
const origin = process.env.BUDDY_UI_TEST_ORIGIN ?? 'http://localhost:8080';
const now = Math.floor(Date.now() / 1000);
const avatar = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" rx="16" fill="#f59e0b"/><text x="16" y="21" text-anchor="middle" fill="white" font-family="Arial" font-size="12">PU</text></svg>';

export const models = Array.from({ length: 100 }, (_, index) => {
  let name = `Preview Model ${String(index + 1).padStart(3, '0')}`;
  let id = `preview-model-${index + 1}`;
  if (index === 0) {
    name = 'Default Preview Model';
    id = 'preview-default-model';
  }
  return {
    id, name, object: 'model', owned_by: 'openai', connection_type: 'external', openai: { id },
    info: {
      id, name, params: {},
      meta: {
        description: 'Isolated mobile layout preview model.',
        capabilities: { vision: true, file_upload: true, tool_calling: true },
        tags: [{ name: index % 2 === 0 ? 'General' : 'Reasoning' }]
      }
    }
  };
});

const chatTitles = [
  'Preview Project Planning', 'Preview Shared Workspace', 'Preview Feature Comparison',
  'Weekend Project Notes', 'Mobile Interface Design', 'Preview Coding Session', 'Preview Resource Planning',
  'Preview Delivery Checklist', 'Product Launch Checklist', 'Weekly Planning', 'Preview Sprint Update',
  'Initial Start', 'Preview Tool Connection', 'Preview Export Setup', 'Could you start by asking me a few questions?'
];
export const chats = Array.from({ length: 25 }, (_, index) => ({
  id: `mobile-preview-chat-${index + 1}`, title: chatTitles[index % chatTitles.length],
  user_id: 'mobile-preview-user', created_at: now - index * 3600, updated_at: now - index * 3600,
  last_read_at: now, pinned: false, active: false, folder_id: null
}));
export const user = {
  id: 'mobile-preview-user', name: 'Preview User', email: 'mobile-preview@example.invalid', role: 'user',
  profile_image_url: `data:image/svg+xml,${encodeURIComponent(avatar)}`,
  permissions: {
    chat: { controls: true, multiple_models: true, temporary: true, stt: true, call: true, file_upload: true },
    features: { web_search: true, image_generation: true, code_interpreter: true }
  },
  expires_at: now + 86400
};
export const config = {
  status: true, name: 'Open WebUI', version: '0.11.4', default_locale: 'en-US', i18n: {}, oauth: { providers: {} },
  features: {
    auth: true, enable_websocket: false, enable_direct_connections: false, enable_folders: true,
    enable_web_search: true, enable_image_generation: true, enable_code_interpreter: true,
    enable_code_execution: false, enable_channels: false, enable_calendar: false, enable_automations: false,
    enable_notes: false, enable_plugins: false, enable_user_status: false, enable_version_update_check: false
  },
  default_models: 'preview-default-model', default_pinned_models: '',
  default_prompt_suggestions: [
    { title: ['Help me plan', 'my next project'], content: 'Could you help me brainstorm ideas for my project?' },
    { title: ['Explain', 'a complex topic'], content: 'Help me understand something new.' },
    { title: ['Write', 'a clear message'], content: 'Help me write a concise and useful message.' }
  ],
  code: { engine: 'pyodide', interpreter_engine: 'pyodide' },
  audio: { tts: { engine: '', voice: '', split_on: 'punctuation' }, stt: { engine: 'web' } },
  file: { max_size: 25, max_count: 10, image_compression: { width: 1024, height: 1024 } },
  permissions: user.permissions, ui: { default_interface_settings: {}, response_watermark: '' }, license_metadata: null
};

export function mockChat(id, options = {}) {
  const summary = chats.find((chat) => chat.id === id) ?? chats[0];
  const prompt = {
    id: 'preview-user-message', parentId: null, childrenIds: ['preview-assistant-message'], role: 'user',
    content: 'Can you help me plan a mobile app that feels simple and polished?', timestamp: now - 60
  };
  const reply = {
    id: 'preview-assistant-message', parentId: prompt.id, childrenIds: [], role: 'assistant',
    model: models[0].id, modelName: models[0].name,
    content: 'Start with the main task people want to finish. Keep text comfortable to read, make controls easy to tap, and give important screens enough room.\n\nA clear chat layout should preserve the conversation while the keyboard opens. Consistent spacing and icon sizes make the interface feel calm and intentional.',
    timestamp: now - 30, done: true,
    usage: { input_tokens: 13000, output_tokens: 1200, total_tokens: 14200, prompt_tokens: 5000, completion_tokens: 200 },
    meta: { local_run: { version: 1, run_id: 'preview-run', started_at: now - 30, completed_at: now - 23, duration_ms: 7000, model_id: models[0].id, status: 'completed', input_tokens: 13000, output_tokens: 1200, total_tokens: 14200, context_tokens: 5200, usage_source: 'provider', usage_complete: true } }
  };
  if (options.usageFixture === 'legacy') delete reply.meta;
  if (options.usageFixture === 'missing') { delete reply.usage; delete reply.meta; }
  if (options.usageFixture === 'zero') {
    Object.assign(reply.meta.local_run, { input_tokens: 0, output_tokens: 0, total_tokens: 0, context_tokens: 0 });
  }
  if (options.usageFixture === 'partial') {
    Object.assign(reply.meta.local_run, { input_tokens: null, output_tokens: 100, total_tokens: null, context_tokens: null, usage_complete: false, status: 'cancelled' });
  }
  if (options.contextCapacity && reply.meta) reply.meta.local_run.context_capacity = { tokens: options.contextCapacity, source: 'configured' };
  if (options.activityFixture === 'waiting') {
    reply.done = false;
    reply.content = '';
    delete reply.usage;
    delete reply.meta;
  }
  return {
    ...summary, current_message_id: reply.id,
    ...(options.compactionThreshold ? { context_usage: { estimated_tokens: 5200, threshold: options.compactionThreshold, source: 'estimated' } } : {}),
    chat: {
      title: summary.title, models: [models[0].id], params: {}, files: [],
      history: { currentId: reply.id, messages: { [prompt.id]: prompt, [reply.id]: reply } }, messages: [prompt, reply]
    }
  };
}

function getApiResponse(url, textScale, options = {}) {
  const path = url.pathname.replace(/\/$/, '');
  if (path === '/api/config') return config;
  if (path === '/api/version') return { version: config.version, deployment_id: 'mobile-preview' };
  if (path === '/api/v1/auths') return user;
  if (path === '/api/models' || path === '/api/models/base') return { object: 'list', data: models };
  if (path === '/api/v1/users/user/settings') {
    return { ui: { textScale, models: [models[0].id], pinnedModels: [], showChatMenu: false, ...(options.pinnedMenuItems ? { pinnedMenuItems: options.pinnedMenuItems } : {}) }, keybindings: {} };
  }
  if (path === '/api/v1/chats') return Number(url.searchParams.get('page') ?? '1') > 1 ? [] : chats;
  if (path === '/api/v1/chats/unread') {
    const unread = chats.filter((chat) =>
      !chat.active && (chat.last_read_at == null || chat.updated_at > chat.last_read_at)
    );
    return { count: unread.length, only_chat_id: unread.length === 1 ? unread[0].id : null };
  }
  if (path === '/api/v1/chats/config') return {};
  if (path === '/api/v1/chats/archived/count') return { count: 0 };
  if (path.startsWith('/api/v1/chats/mobile-preview-chat-') && !path.endsWith('/tags')) return mockChat(path.split('/')[4], options);
  if (path.startsWith('/api/tasks/chat/')) {
    if (options.activityFixture === 'waiting') return { task_ids: ['preview-active-task'] };
    return { task_ids: [] };
  }
  if (path.includes('/configs/connections')) return { OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {} };
  if (path.includes('/tool_permissions')) return { mode: 'allow', permissions: {} };
  if (path.includes('/models/model/') && !path.endsWith('/image')) return models[0].info;
  return [];
}

export async function createMockedPage(options = {}) {
  mkdirSync(outputDirectory, { recursive: true });
  let browserType = chromium;
  if (options.browserName === 'webkit') browserType = webkit;
  const browser = await browserType.launch({ headless: true });
  const context = await browser.newContext({
    viewport: options.viewport ?? { width: 390, height: 844 }, hasTouch: options.hasTouch ?? true, isMobile: options.isMobile ?? true,
    deviceScaleFactor: 1, locale: 'en-US', colorScheme: options.colorScheme ?? 'light', serviceWorkers: 'block'
  });
  const requests = [];
  const errors = [];
  const blockedMutations = [];
  await context.addInitScript((theme) => {
    localStorage.setItem('token', 'mobile-preview-fake-token');
    localStorage.setItem('locale', 'en-US');
    localStorage.setItem('theme', theme);
    localStorage.setItem('sidebar', 'false');
    localStorage.setItem('sidebarWidth', '245');
    localStorage.setItem('changelog', '0.11.4');
  }, options.colorScheme === 'dark' ? 'dark' : 'light');
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin && url.protocol !== 'data:' && url.protocol !== 'blob:') {
      await route.abort('blockedbyclient');
      return;
    }
    if (url.pathname.startsWith('/ws/')) {
      await route.abort('blockedbyclient');
      return;
    }
    if (url.pathname.startsWith('/ollama/')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: '0.0.0', models: [] }) });
      return;
    }
    if (!url.pathname.startsWith('/api/')) {
      if (request.method() !== 'GET' && request.method() !== 'HEAD') {
        blockedMutations.push(request.method() + ' ' + url.pathname);
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        return;
      }
      await route.continue();
      return;
    }
    requests.push(`${request.method()} ${url.pathname}${url.search}`);
    if (request.method() !== 'GET') {
      blockedMutations.push(`${request.method()} ${url.pathname}`);
      let response = { status: true, ...user };
      if (/^\/api\/v1\/chats\/mobile-preview-chat-\d+\/?$/.test(url.pathname)) {
        response = mockChat(url.pathname.split('/')[4], options);
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
      return;
    }
    if (url.pathname.endsWith('/profile/image')) {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: avatar });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(getApiResponse(url, options.textScale ?? 1, options)) });
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.stack ?? error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto(`${origin}${options.urlPath ?? '/'}`, { waitUntil: 'domcontentloaded' });
  try {
    await page.locator('#chat-input').waitFor({ state: 'visible', timeout: 30000 });
    await page.locator('#model-selector-model-button').waitFor({ state: options.modelSelectorVisible === false ? 'attached' : 'visible', timeout: 10000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(250);
  } catch (error) {
    await page.screenshot({ path: resolve(outputDirectory, 'mobile-preview-startup-failure.png') });
    writeFileSync(resolve(outputDirectory, 'mobile-preview-startup-failure.json'), JSON.stringify({ url: page.url(), body: await page.locator('body').innerText(), requests, errors }, null, 2));
    await browser.close();
    throw error;
  }
  const usagePath = resolve(workspace, 'local/web/chat-usage-info.js');
  if (options.usage !== false && existsSync(usagePath)) await page.addScriptTag({ content: readFileSync(usagePath, 'utf8') });
  const polishPath = resolve(workspace, 'local/web/mobile-ui-polish.js');
  if (options.polish !== false && existsSync(polishPath)) {
    await page.addScriptTag({ content: readFileSync(polishPath, 'utf8') });
    await page.waitForTimeout(100);
  }
  return { browser, context, page, requests, errors, blockedMutations };
}

export async function capturePreview(options = {}) {
  mkdirSync(outputDirectory, { recursive: true });
  const session = await createMockedPage(options);
  let prefix = options.prefix ?? 'mobile-polished';
  if (!options.prefix && options.polish === false) prefix = 'mobile-baseline';
  const { page } = session;
  const screenshots = [];
  try {
    const chatPath = resolve(outputDirectory, `${prefix}-chat.png`);
    await page.screenshot({ path: chatPath });
    screenshots.push(chatPath);
    await page.locator('#sidebar-toggle-button').click();
    await page.locator('#sidebar-chat-item').first().waitFor({ state: 'visible' });
    await page.waitForTimeout(300);
    const sidebarPath = resolve(outputDirectory, `${prefix}-sidebar.png`);
    await page.screenshot({ path: sidebarPath });
    screenshots.push(sidebarPath);
    await page.mouse.click(350, 350);
    await page.waitForTimeout(300);
    await page.locator('#model-selector-model-button').click();
    await page.locator('#model-search-input').waitFor({ state: 'visible' });
    await page.waitForTimeout(250);
    const modelsPath = resolve(outputDirectory, `${prefix}-models.png`);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(150);
    await page.screenshot({ path: modelsPath });
    screenshots.push(modelsPath);
    const result = {
      url: page.url(), screenshots, requests: session.requests, errors: session.errors,
      blockedMutations: session.blockedMutations, body: (await page.locator('body').innerText()).slice(0, 1800)
    };
    writeFileSync(resolve(outputDirectory, `${prefix}-result.json`), JSON.stringify(result, null, 2));
    return result;
  } finally {
    await session.browser.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const baseline = process.argv.includes('--baseline');
  let browserName = 'chromium';
  if (process.argv.includes('--webkit')) browserName = 'webkit';
  const result = await capturePreview({ polish: !baseline, browserName });
  console.log(JSON.stringify(result, null, 2));
}
