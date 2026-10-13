import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import i18n, { initI18n } from '../../src/lib/i18n';

const generalTitle = 'settings.personal.general.title';

describe('settings language fallback', () => {
	it('loads English labels for a saved generic English locale', async () => {
		await initI18n('en');
		expect(get(i18n).t(generalTitle)).toBe('General');
	});

	it('loads English labels for an unsupported regional locale', async () => {
		await initI18n('en-AU');
		expect(get(i18n).t(generalTitle)).toBe('General');
	});

	it('preserves supported translations', async () => {
		await initI18n('es-ES');
		expect(get(i18n).t('settings.personal.interface.title')).toBe('Interfaz');
	});

	it('preserves English overrides when the saved locale needs a fallback', async () => {
		await initI18n('en', { 'en-US': { [generalTitle]: 'Custom General' } });
		expect(get(i18n).t(generalTitle)).toBe('Custom General');
	});
});
