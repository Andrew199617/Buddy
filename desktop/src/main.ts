import {
	app,
	BrowserWindow,
	dialog,
	ipcMain,
	Menu,
	nativeImage,
	nativeTheme,
	net,
	safeStorage,
	session,
	shell,
	Tray,
	type IpcMainInvokeEvent,
	type MenuItemConstructorOptions
} from 'electron';
import { spawn } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { BRIDGE_CHANNELS } from './bridge-channels';
import { CredentialStore } from './credential-store';
import { DesktopService, type LoginItem } from './desktop-service';
import { enrollComputer, unenrollComputer } from './enrollment';
import { registerBuddyBridge, registerSetupBridge } from './ipc';
import { createNativeDialogs } from './native-dialogs';
import { RunnerSupervisor, type RunnerLaunch } from './runner-supervisor';
import { checkBuddyServer, normalizeServerAddress } from './server-address';
import { SettingsStore } from './settings-store';
import type { DesktopStatus } from './status';
import { allowsPermission, isSameOrigin, isWebLink } from './web-security';

// Started at sign-in, the app waits in the taskbar instead of opening a window.
const HIDDEN_ARGUMENT = '--hidden';
const PAGES_DIRECTORY = path.join(__dirname, '..', '..', 'pages');
const SETUP_PAGE = path.join(PAGES_DIRECTORY, 'setup.html');
const OFFLINE_PAGE = path.join(PAGES_DIRECTORY, 'offline.html');
// Chromium's code for a load that was replaced by another navigation.
const LOAD_ABORTED = -3;

let service: DesktopService;
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let shutDown = false;

function userDataPath(name: string): string {
	return path.join(app.getPath('userData'), name);
}

function iconPath(): string {
	if (app.isPackaged) return path.join(process.resourcesPath, 'icons', 'buddy.png');
	return path.join(__dirname, '..', '..', '..', 'static', 'static', 'favicon.png');
}

/** The bundled buddy-runner.exe, or in development the Runner in this repository. */
function runnerLaunch(): RunnerLaunch {
	if (app.isPackaged) {
		return { command: path.join(process.resourcesPath, 'runner', 'buddy-runner.exe'), args: [] };
	}
	// To try a build from build-runner.ps1 without packaging the app.
	if (process.env.BUDDY_DESKTOP_RUNNER) {
		return { command: process.env.BUDDY_DESKTOP_RUNNER, args: [] };
	}
	const backend = path.join(__dirname, '..', '..', '..', 'backend');
	return {
		command: process.env.BUDDY_DESKTOP_PYTHON || 'python',
		args: ['-P', '-m', 'buddy_runner'],
		env: { PYTHONPATH: backend, PYTHONUNBUFFERED: '1' }
	};
}

const loginItem: LoginItem = {
	isEnabled: () => app.getLoginItemSettings({ args: [HIDDEN_ARGUMENT] }).openAtLogin,
	setEnabled: (enabled) => app.setLoginItemSettings({ openAtLogin: enabled, args: [HIDDEN_ARGUMENT] })
};

function createService(): DesktopService {
	const supervisor = new RunnerSupervisor({
		launch: runnerLaunch(),
		stateDir: userDataPath('runner'),
		logFile: userDataPath('runner.log'),
		spawnRunner: spawn
	});
	const accountName = os.userInfo().username;
	return new DesktopService({
		settings: new SettingsStore(userDataPath('settings.json')),
		credentials: new CredentialStore(userDataPath('machine-credential.bin'), safeStorage),
		supervisor,
		dialogs: createNativeDialogs(() => mainWindow, accountName),
		loginItem,
		enroll: (serverUrl, token, host) => enrollComputer(serverUrl, token, host, net.fetch),
		unenroll: (serverUrl, machineId, credential) =>
			unenrollComputer(serverUrl, machineId, credential, net.fetch),
		platform: process.platform,
		appVersion: app.getVersion(),
		hostName: os.hostname(),
		accountName
	});
}

// --- Pages -----------------------------------------------------------------

function isBuddyPage(event: IpcMainInvokeEvent): boolean {
	const serverUrl = service.serverUrl;
	const frame = event.senderFrame;
	if (!serverUrl || !frame || frame !== event.sender.mainFrame) return false;
	return isSameOrigin(frame.url, serverUrl);
}

function isSetupPage(event: IpcMainInvokeEvent): boolean {
	const frame = event.senderFrame;
	if (!frame || frame !== event.sender.mainFrame) return false;
	return frame.url === pathToFileURL(SETUP_PAGE).href;
}

function openExternally(url: string): void {
	if (isWebLink(url)) void shell.openExternal(url);
}

/** The app shows only Buddy itself; other links open in the system browser. */
function keepToBuddy(event: Electron.Event, url: string): void {
	const serverUrl = service.serverUrl;
	if (serverUrl && isSameOrigin(url, serverUrl)) return;
	event.preventDefault();
	openExternally(url);
}

function guardWebContents(contents: Electron.WebContents): void {
	contents.on('will-attach-webview', (event) => event.preventDefault());
	contents.on('will-navigate', keepToBuddy);
	contents.on('will-redirect', keepToBuddy);
	contents.setWindowOpenHandler(({ url }) => {
		const serverUrl = service.serverUrl;
		if (serverUrl && isSameOrigin(url, serverUrl)) {
			return { action: 'allow', overrideBrowserWindowOptions: buddyWindowOptions(serverUrl) };
		}
		openExternally(url);
		return { action: 'deny' };
	});
}

function guardPermissions(): void {
	session.defaultSession.setPermissionRequestHandler((_contents, permission, callback, details) => {
		const serverUrl = service.serverUrl;
		callback(Boolean(serverUrl) && allowsPermission(permission, details.requestingUrl, serverUrl!));
	});
	session.defaultSession.setPermissionCheckHandler((_contents, permission, requestingOrigin) => {
		const serverUrl = service.serverUrl;
		return Boolean(serverUrl) && allowsPermission(permission, requestingOrigin, serverUrl!);
	});
}

function buddyWindowOptions(serverUrl: string): Electron.BrowserWindowConstructorOptions {
	let backgroundColor = '#ffffff';
	if (nativeTheme.shouldUseDarkColors) backgroundColor = '#171717';
	return {
		width: 1200,
		height: 820,
		minWidth: 380,
		minHeight: 520,
		title: 'Buddy',
		icon: iconPath(),
		show: false,
		autoHideMenuBar: true,
		backgroundColor,
		webPreferences: {
			preload: path.join(__dirname, 'preload.js'),
			contextIsolation: true,
			sandbox: true,
			nodeIntegration: false,
			webviewTag: false,
			additionalArguments: [`--buddy-origin=${serverUrl}`]
		}
	};
}

function showOfflinePage(window: BrowserWindow, serverUrl: string): void {
	void window.loadFile(OFFLINE_PAGE, { query: { server: serverUrl } });
}

function openBuddyWindow(serverUrl: string, show: boolean): BrowserWindow {
	const window = new BrowserWindow(buddyWindowOptions(serverUrl));
	if (show) window.once('ready-to-show', () => window.show());
	window.on('close', (event) => {
		// A connected computer stays available from the taskbar.
		if (!quitting && service.isConnected) {
			event.preventDefault();
			window.hide();
		}
	});
	window.on('closed', () => {
		if (mainWindow === window) mainWindow = null;
	});
	window.webContents.on('did-fail-load', (_event, errorCode, _description, _url, isMainFrame) => {
		if (isMainFrame && errorCode !== LOAD_ABORTED) showOfflinePage(window, serverUrl);
	});
	void window.loadURL(serverUrl);
	return window;
}

function openSetupWindow(): BrowserWindow {
	const window = new BrowserWindow({
		width: 520,
		height: 560,
		resizable: false,
		title: 'Buddy',
		icon: iconPath(),
		autoHideMenuBar: true,
		webPreferences: {
			preload: path.join(__dirname, 'setup-preload.js'),
			contextIsolation: true,
			sandbox: true,
			nodeIntegration: false,
			webviewTag: false
		}
	});
	window.on('closed', () => {
		if (mainWindow === window) mainWindow = null;
	});
	void window.loadFile(SETUP_PAGE);
	return window;
}

function replaceMainWindow(next: BrowserWindow): void {
	const previous = mainWindow;
	mainWindow = next;
	if (previous && !previous.isDestroyed()) previous.destroy();
}

function showMainWindow(): void {
	if (!mainWindow || mainWindow.isDestroyed()) {
		const serverUrl = service.serverUrl;
		if (serverUrl) {
			mainWindow = openBuddyWindow(serverUrl, true);
		} else {
			mainWindow = openSetupWindow();
		}
		return;
	}
	if (mainWindow.isMinimized()) mainWindow.restore();
	mainWindow.show();
	mainWindow.focus();
}

async function saveAddress(address: string): Promise<{ error?: string }> {
	let serverUrl: string;
	try {
		serverUrl = normalizeServerAddress(address);
		await checkBuddyServer(serverUrl, net.fetch);
	} catch (error) {
		return { error: (error as Error).message };
	}
	await service.changeServer(serverUrl);
	replaceMainWindow(openBuddyWindow(serverUrl, true));
	return {};
}

async function changeAddress(): Promise<void> {
	if (service.isConnected) {
		const { response } = await dialog.showMessageBox({
			type: 'warning',
			title: 'Change Buddy address',
			message: 'Changing the address disconnects this computer.',
			detail: 'You can connect it to the new address afterwards, in Settings → Machines.',
			buttons: ['Change address', 'Cancel'],
			defaultId: 1,
			cancelId: 1,
			noLink: true
		});
		if (response !== 0) return;
	}
	replaceMainWindow(openSetupWindow());
}

function openClaudeTokenSetup(): void {
	if (process.platform !== 'win32') {
		throw new Error('Open a terminal and run: claude setup-token');
	}
	// A visible terminal, so the sign-in happens where the person can see it.
	const terminal = spawn('cmd.exe', ['/d', '/c', 'start', 'Claude sign-in', 'cmd.exe', '/k', 'claude setup-token'], {
		detached: true,
		stdio: 'ignore'
	});
	terminal.unref();
}

// --- Taskbar ---------------------------------------------------------------

function trayStatusLabel(status: DesktopStatus): string {
	if (status.state === 'connected') return `Connected as ${status.machineName ?? 'this computer'}`;
	if (status.state === 'connecting') return 'Connecting…';
	if (status.state === 'offline') return 'Offline, reconnecting';
	if (status.state === 'not_connected') return 'This computer is not connected';
	return 'This computer is disconnected';
}

function quitBuddy(): void {
	quitting = true;
	app.quit();
}

function updateTray(status: DesktopStatus): void {
	if (!tray) return;
	const items: MenuItemConstructorOptions[] = [
		{ label: trayStatusLabel(status), enabled: false },
		{ type: 'separator' },
		{ label: 'Open Buddy', click: showMainWindow },
		{ label: 'Change Buddy address…', click: () => void changeAddress() },
		{ label: 'Open Runner log', click: () => void shell.openPath(userDataPath('runner.log')) },
		{ type: 'separator' },
		{ label: 'Quit Buddy', click: quitBuddy }
	];
	tray.setContextMenu(Menu.buildFromTemplate(items));
	tray.setToolTip(`Buddy: ${trayStatusLabel(status)}`);
}

function createTray(): void {
	const icon = nativeImage.createFromPath(iconPath()).resize({ width: 16, height: 16 });
	tray = new Tray(icon);
	tray.on('click', showMainWindow);
	updateTray(service.getStatus());
}

function setApplicationMenu(): void {
	const template: MenuItemConstructorOptions[] = [
		{
			label: 'Buddy',
			submenu: [
				{ label: 'Change Buddy address…', click: () => void changeAddress() },
				{ type: 'separator' },
				{ label: 'Quit Buddy', click: quitBuddy }
			]
		},
		{ role: 'editMenu' },
		{ role: 'viewMenu' },
		{ role: 'windowMenu' }
	];
	Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function sendStatusToBuddy(status: DesktopStatus): void {
	const serverUrl = service.serverUrl;
	if (!serverUrl) return;
	for (const window of BrowserWindow.getAllWindows()) {
		if (isSameOrigin(window.webContents.getURL(), serverUrl)) {
			window.webContents.send(BRIDGE_CHANNELS.status, status);
		}
	}
}

// --- Lifecycle -------------------------------------------------------------

function startApp(): void {
	app.setAppUserModelId('com.buddy.desktop');
	service = createService();
	service.on('status', (status: DesktopStatus) => {
		sendStatusToBuddy(status);
		updateTray(status);
	});
	app.on('web-contents-created', (_event, contents) => guardWebContents(contents));
	guardPermissions();
	registerBuddyBridge(ipcMain, service, isBuddyPage, { openClaudeTokenSetup });
	registerSetupBridge(ipcMain, isSetupPage, { currentAddress: () => service.serverUrl, saveAddress });
	setApplicationMenu();
	createTray();
	service.start();

	const startedHidden = process.argv.includes(HIDDEN_ARGUMENT);
	const serverUrl = service.serverUrl;
	if (!serverUrl) {
		mainWindow = openSetupWindow();
	} else if (!startedHidden) {
		mainWindow = openBuddyWindow(serverUrl, true);
	}
}

// Development and tests may keep the app's data in a throwaway folder.
if (!app.isPackaged && process.env.BUDDY_DESKTOP_USER_DATA) {
	app.setPath('userData', process.env.BUDDY_DESKTOP_USER_DATA);
}

if (!app.requestSingleInstanceLock()) {
	app.quit();
} else {
	app.on('second-instance', showMainWindow);
	app.on('window-all-closed', () => {
		// A connected computer keeps running from the taskbar.
		if (!service.isConnected) quitBuddy();
	});
	app.on('before-quit', (event) => {
		quitting = true;
		if (shutDown) return;
		// Stop Claude Code and Codex and say goodbye to Buddy before exiting.
		event.preventDefault();
		void service.shutdown().finally(() => {
			shutDown = true;
			app.quit();
		});
	});
	void app.whenReady().then(startApp);
}
