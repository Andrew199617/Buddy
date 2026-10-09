import tailwindcss from '@tailwindcss/postcss';
import buddyFontSizeOffset from './local/font-size-offset.mjs';

export default {
	plugins: [tailwindcss(), buddyFontSizeOffset()]
};
