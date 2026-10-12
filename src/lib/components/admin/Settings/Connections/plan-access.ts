// What each plan access level lets Claude or ChatGPT do, worded once so
// Settings → Machines and a chat folder's settings tell people the same thing.

type Translator = { t: (key: string, options?: Record<string, unknown>) => string };

export function readFilesDescription(i18n: Translator, providerId: string): string {
	if (providerId === 'codex') {
		return i18n.t(
			"Can run read-only commands. Codex's read-only sandbox does not limit reads to the start folder, so it can read any file your account can. No edits."
		);
	}
	return i18n.t(
		'Can read and search files and the web using CLI permissions on the selected computer. Reads are not restricted to the start folder by an OS sandbox. No edits or commands.'
	);
}

export function fullAccessDescription(i18n: Translator): string {
	return i18n.t(
		'Can edit files and run terminal commands on the machine as you, without asking first.'
	);
}

/** Said once a plan has the access the person allowed. */
export function accessGrantedMessage(
	i18n: Translator,
	planName: string,
	access: string,
	computerName: string
): string {
	if (access === 'full') {
		return i18n.t('{{name}} now has full access on {{computer}}.', {
			name: planName,
			computer: computerName
		});
	}
	return i18n.t('{{name}} can now read files on {{computer}}.', {
		name: planName,
		computer: computerName
	});
}

export function fullAccessWarning(i18n: Translator): string {
	return i18n.t(
		'Buddy can then run commands on that computer as your account there, and so can anyone who controls this Buddy server.'
	);
}
