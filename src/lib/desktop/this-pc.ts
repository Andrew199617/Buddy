import { readable, type Readable } from 'svelte/store';

import type { BuddyDesktop, DesktopStatus } from './bridge';
import { getDesktop } from './bridge';

// Which of the person's computers this page runs on: the Buddy app's own
// computer, once it is connected. Null in an ordinary browser, or while the
// app's computer is not connected. Only this ever makes a computer "Local";
// Buddy's own 'local' machine id means Buddy server, never this PC.

function connectedMachineId(status: DesktopStatus | null): string | null {
	if (!status?.machineId) return null;
	return status.machineId;
}

export function thisPcStore(desktop: BuddyDesktop | null): Readable<string | null> {
	return readable<string | null>(null, (set) => {
		if (!desktop) return;
		let stopped = false;
		// A change heard first is newer than the first answer, which is then ignored.
		let heardChange = false;
		desktop
			.getStatus()
			.then((status) => {
				if (!stopped && !heardChange) set(connectedMachineId(status));
			})
			.catch(() => {
				if (!stopped && !heardChange) set(null);
			});
		const stopListening = desktop.onStatusChanged((status) => {
			heardChange = true;
			set(connectedMachineId(status));
		});
		return () => {
			stopped = true;
			stopListening();
		};
	});
}

export const thisPcMachineId = thisPcStore(getDesktop());
