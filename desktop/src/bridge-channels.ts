// IPC channels between the app and its pages. The sandboxed preloads cannot
// import this file, so preload.ts and setup-preload.ts repeat these names;
// a test checks that they match.

export const BRIDGE_CHANNELS = {
	getStatus: 'buddy-desktop:get-status',
	connect: 'buddy-desktop:connect',
	disconnect: 'buddy-desktop:disconnect',
	chooseFolder: 'buddy-desktop:choose-folder',
	chooseProjectFolder: 'buddy-desktop:choose-project-folder',
	removeFolder: 'buddy-desktop:remove-folder',
	setPermission: 'buddy-desktop:set-permission',
	setStartAtLogin: 'buddy-desktop:set-start-at-login',
	openClaudeTokenSetup: 'buddy-desktop:open-claude-token-setup',
	status: 'buddy-desktop:status'
} as const;

export const SETUP_CHANNELS = {
	getAddress: 'buddy-setup:get-address',
	saveAddress: 'buddy-setup:save-address'
} as const;
