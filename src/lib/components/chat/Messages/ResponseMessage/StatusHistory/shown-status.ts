// Which of a reply's status lines its status history shows.

/** A status line as the server sends it and saves it in `message.statusHistory`. */
export type StatusLine = {
	description?: string;
	done?: boolean;
	// Set on the line that ends a reply, so the history shows nothing then.
	hidden?: boolean;
	// Set on a line that stays shown once the reply ends.
	keep?: boolean;
	[field: string]: unknown;
};

/**
 * The line a reply's status history shows: its latest one. When the latest is
 * hidden, as the line that ends a reply is, the latest line marked keep shows
 * instead, so "Ultra is off for this reply: …" still says why the reply ran
 * without workers. Null when there are no lines.
 */
export function shownStatus(history: StatusLine[] | null | undefined): StatusLine | null {
	if (!history || history.length === 0) return null;
	const latest = history[history.length - 1];
	if (latest?.hidden !== true) return latest;
	for (let index = history.length - 2; index >= 0; index -= 1) {
		if (history[index]?.keep === true) return history[index];
	}
	return latest;
}
