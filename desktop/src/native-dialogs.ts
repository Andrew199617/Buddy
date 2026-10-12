import { dialog, type BrowserWindow, type MessageBoxOptions } from 'electron';

import type { Dialogs } from './desktop-service';
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

function connectQuestion(serverUrl: string, accountName: string): MessageBoxOptions {
	return {
		title: 'Connect this computer',
		message: `Let ${new URL(serverUrl).host} run Claude Code and Codex on this computer?`,
		detail:
			`They run as ${accountName}, with access to your files and apps. Anyone who controls ` +
			'that Buddy server can use them through this connection. You can disconnect at any time ' +
			"in Buddy's Settings → Machines or from the Buddy icon in the taskbar.",
		buttons: ['Connect', 'Cancel']
	};
}

function permissionQuestion(permission: DesktopPermission, accountName: string): MessageBoxOptions {
	if (permission === 'allowEdits') {
		return {
			title: 'Allow editing files',
			message: 'Let Buddy edit files in your shared folders?',
			detail:
				'The Projects page can then save changes to existing text files in the folders you ' +
				'shared. Changing project files can also lead to running code, for example through ' +
				'build scripts.',
			buttons: ['Allow editing', 'Cancel']
		};
	}
	return {
		title: 'Allow running commands',
		message: 'Let Buddy run commands on this computer?',
		detail:
			`Commands from the Projects page run as ${accountName}, starting in your shared ` +
			'folders. A shared folder is not a sandbox: a command can reach anything your account can.',
		buttons: ['Allow commands', 'Cancel']
	};
}

export function createNativeDialogs(parentWindow: () => BrowserWindow | null, accountName: string): Dialogs {
	return {
		confirmConnect: (serverUrl, account) => ask(parentWindow(), connectQuestion(serverUrl, account)),

		chooseFolder: async () => {
			const parent = parentWindow();
			const options: Electron.OpenDialogOptions = {
				title: 'Share a folder with Buddy',
				buttonLabel: 'Share folder',
				properties: ['openDirectory']
			};
			let result;
			if (parent) {
				result = await dialog.showOpenDialog(parent, options);
			} else {
				result = await dialog.showOpenDialog(options);
			}
			if (result.canceled || result.filePaths.length === 0) return null;
			return result.filePaths[0];
		},

		confirmPermission: (permission) => ask(parentWindow(), permissionQuestion(permission, accountName))
	};
}
