// Keep interface and desktop theme changes in sync with Buddy's design tokens.
export function applyAppTheme(selectedTheme: string): void {
	const root = document.documentElement;
	let resolvedTheme = selectedTheme;
	let themeColor = '#FAF8F5';

	if (selectedTheme === 'system') {
		resolvedTheme = 'light';
		if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
			resolvedTheme = 'dark';
		}
	} else if (selectedTheme === 'oled-dark') {
		resolvedTheme = 'dark';
	} else if (selectedTheme === 'her') {
		resolvedTheme = 'light';
	}

	root.classList.remove('dark', 'light', 'oled-dark', 'her');
	root.classList.add(resolvedTheme);

	if (selectedTheme === 'oled-dark') {
		root.style.setProperty('--color-gray-800', '#101010');
		root.style.setProperty('--color-gray-850', '#050505');
		root.style.setProperty('--color-gray-900', '#000000');
		root.style.setProperty('--color-gray-950', '#000000');
		themeColor = '#000000';
	} else {
		root.style.removeProperty('--color-gray-800');
		root.style.removeProperty('--color-gray-850');
		root.style.removeProperty('--color-gray-900');
		root.style.removeProperty('--color-gray-950');
		if (resolvedTheme === 'dark') {
			themeColor = '#111512';
		}
	}

	if (selectedTheme === 'her') {
		root.classList.add('her');
		themeColor = '#983724';
	}

	document.querySelector('meta[name="theme-color"]')?.setAttribute('content', themeColor);

	const themeWindow = window as Window & { applyTheme?: () => void };
	if (themeWindow.applyTheme) {
		themeWindow.applyTheme();
	}
}
