import dayjs from 'dayjs';

// Vite emits each locale separately. English is already part of Day.js.
const localeModules = import.meta.glob('../../node_modules/dayjs/locale/*.js');
let latestLocaleRequest = 0;

export const loadDateLocale = async (locale) => {
	const request = ++latestLocaleRequest;
	const normalized = (locale || 'en').toLowerCase().replaceAll('_', '-');
	let resolved = normalized;
	let loader = localeModules['../../node_modules/dayjs/locale/' + resolved + '.js'];
	if (!loader) {
		resolved = normalized.split('-')[0];
		loader = localeModules['../../node_modules/dayjs/locale/' + resolved + '.js'];
	}
	if (!loader && resolved !== 'en') {
		resolved = 'en';
	}
	if (resolved !== 'en' && loader) {
		try {
			await loader();
		} catch (error) {
			console.warn('Date locale could not load; using English dates.', error);
			if (request === latestLocaleRequest) {
				dayjs.locale('en');
			}
			return;
		}
	}
	if (request === latestLocaleRequest) {
		dayjs.locale(resolved);
	}
};

export default dayjs;
