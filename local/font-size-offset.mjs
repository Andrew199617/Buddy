// Increase text sizes without changing the rem-based layout or UI Scale setting.
const fontOffset = 'var(--buddy-font-size-offset, 2px)';

export function addFontSizeOffset(value) {
	const original = value.trim();
	if (/^(inherit|initial|unset|revert|revert-layer|0)$/.test(original)) {
		return value;
	}
	if (original.includes('--buddy-font-size-offset')) {
		return value;
	}

	// The parent already includes the offset. Remove its contribution from
	// em/percentage sizes before adding this element's own two pixels.
	const relativeSizes = original.replace(
		/(?<![\w.-])(\d*\.?\d+)(em|%)(?![\w-])/g,
		(match, amount, unit) => {
			let multiplier = Number(amount);
			if (unit === '%') {
				multiplier = multiplier / 100;
			}
			return 'calc(' + multiplier + 'em - ' + multiplier + ' * ' + fontOffset + ')';
		}
	);
	return 'calc(' + relativeSizes + ' + ' + fontOffset + ')';
}

function offsetFontSizeDeclaration(declaration) {
	const rule = declaration.parent;
	if (rule.type !== 'rule') {
		return;
	}
	if (rule.selectors.some((selector) => ['html', ':root', ':host'].includes(selector.trim()))) {
		return;
	}

	// Math glyphs and the PDF selection layer use coordinated document
	// coordinates rather than app UI typography.
	const sourceFile = declaration.source?.input?.file || '';
	if (
		sourceFile.includes('katex') ||
		rule.selector.includes('.katex') ||
		rule.selector.includes('.textLayer')
	) {
		return;
	}
	declaration.value = addFontSizeOffset(declaration.value);
}

export default function buddyFontSizeOffset() {
	const plugin = {
		postcssPlugin: 'buddy-font-size-offset',
		OnceExit(root) {
			root.walkDecls('font-size', offsetFontSizeDeclaration);
		}
	};
	return plugin;
}
