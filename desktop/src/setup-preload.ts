// The local "Your Buddy address" page. Sandboxed, so the channel names repeat
// those in bridge-channels.ts.

import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('buddySetup', {
	getAddress: (): Promise<string | null> => ipcRenderer.invoke('buddy-setup:get-address'),
	saveAddress: (address: unknown): Promise<{ error?: string }> =>
		ipcRenderer.invoke('buddy-setup:save-address', String(address))
});
