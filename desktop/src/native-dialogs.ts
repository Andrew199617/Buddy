import { dialog, type BrowserWindow, type MessageBoxOptions } from 'electron';

import type { ConnectAnswer, Dialogs } from './desktop-service';
import type { DesktopPermission } from './status';

// The app's own questions. They come from Windows, not from the web page, so a
// Buddy server cannot answer them or change what they say.

async function ask(parent: BrowserWindow | null, options: MessageBoxOptions): Promise<boolean> {
	const question: MessageBoxOptions = { type: 'warning', noLink: true, defaultId: 1, cancelId: 1, ...options };
	let result;
	if (parent) {
		result = await dialog.showMessageBox(parent, question);
	} else {
		result = await dialog.showMessageBox(question);
	}
	return result.response === 0;
}

/** The connect question's answer, with its checkbox; Cancel connects nothing. */
async function askToConnect(parent: BrowserWindow | null, options: MessageBoxOptions): Promise<ConnectAnswer> {
	const question: MessageBoxOptions = { type: 'warning', noLink: true, defaultId: 1, cancelId: 1, ...options };
	let result;
	if (parent) {
		result = await dialog.showMessageBox(parent, question);
	} else {
		result = await dialog.showMessageBox(question);
	}
	return { connect: result.response === 0, allowFolderBrowsing: result.checkboxChecked === true };
}

function connectQuestion(serverUrl: string, accountName: string): MessageBoxOptions {
	return {
		title: 'Connect this computer',
		message: `Let ${new URL(serverUrl).host} run Claude Code and Codex on this computer?`,
		detail:
			`They run as ${accountName}, with access to your files and apps. Anyone who controls ` +
			'that Buddy server can use them through this connection. You can disconnect at any time ' +
			"in Buddy's Settings → Machines or from the Buddy icon in the taskbar.",
		buttons: ['Connect', 'Cancel'],
		checkboxLabel: 'Let Buddy suggest and browse folders on this computer (names only)',
		checkboxChecked: true
	};
}

function permissionQuestion(permission: DesktopPermission, accountName: string): MessageBoxOptions {
	if (permission === 'allowFolderBrowsing') {
		return {
			title: 'Allow folder suggestions',
			message: 'Let Buddy list folder names on this computer?',
			detail:
				'You can then pick project folders from the web and your phone, and Buddy suggests the ' +
				'folders Claude Code and Codex worked in. Buddy sees folder names only, never file contents.',
			buttons: ['Allow', 'Cancel']
		};
	}
	if (permission === 'allowEdits') {
		return {
			title: 'Allow editing files',
			message: 'Let Buddy edit files in your shared folders?',
			detail:
				"A chat's Project tab in Buddy can then save changes to existing text files in the " +
				'folders you shared. Changing project files can also lead to running code, for example ' +
				'through build scripts.',
			buttons: ['Allow editing', 'Cancel']
		};
	}
	return {
		title: 'Allow running commands',
		message: 'Let Buddy run commands on this computer?',
		detail:
			`Commands from a chat's Project tab in Buddy run as ${accountName}, starting in your ` +
			'shared folders. A shared folder is not a sandbox: a command can reach anything your ' +
			'account can.',
		buttons: ['Allow commands', 'Cancel']
	};
}

async function pickFolder(
	parent: BrowserWindow | null,
	options: Electron.OpenDialogOptions
): Promise<string | null> {
	let result;
	if (parent) {
		result = await dialog.showOpenDialog(parent, options);
	} else {
		result = await dialog.showOpenDialog(options);
	}
	if (result.canceled || result.filePaths.length === 0) return null;
	return result.filePaths[0];
}

export function createNativeDialogs(parentWindow: () => BrowserWindow | null, accountName: string): Dialogs {
	return {
		confirmConnect: (serverUrl, account) => askToConnect(parentWindow(), connectQuestion(serverUrl, account)),

		chooseFolder: () =>
			pickFolder(parentWindow(), {
				title: 'Share a folder with Buddy',
				buttonLabel: 'Share folder',
				properties: ['openDirectory']
			}),

		chooseProjectFolder: () =>
			pickFolder(parentWindow(), {
				title: 'Choose a project folder',
				buttonLabel: 'Use folder',
				properties: ['openDirectory']
			}),

		confirmPermission: (permission) => ask(parentWindow(), permissionQuestion(permission, accountName))
	};
}
