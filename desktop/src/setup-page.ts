// Script for pages/setup.html. A plain script, not a module, so it runs as is.

interface Window {
	buddySetup: {
		getAddress(): Promise<string | null>;
		saveAddress(address: string): Promise<{ error?: string }>;
	};
}

function setUpAddressForm(): void {
	const form = document.getElementById('address-form') as HTMLFormElement;
	const input = document.getElementById('address') as HTMLInputElement;
	const button = document.getElementById('continue') as HTMLButtonElement;
	const error = document.getElementById('address-error') as HTMLParagraphElement;

	window.buddySetup.getAddress().then((address) => {
		if (address) input.value = address;
		input.focus();
	});

	form.addEventListener('submit', async (event) => {
		event.preventDefault();
		button.disabled = true;
		button.textContent = 'Checking…';
		error.textContent = '';
		const result = await window.buddySetup.saveAddress(input.value);
		if (result.error) {
			error.textContent = result.error;
			button.disabled = false;
			button.textContent = 'Continue';
			input.focus();
		}
	});
}

setUpAddressForm();
