<script lang="ts">
	import { getContext } from 'svelte';
	import type { Placement } from 'tippy.js';
	import { mobile, showSidebar } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n: any = getContext('i18n');

	export let placement: Placement = 'top';
	export let interactive = false;

	let tooltipContent = '';
	$: {
		tooltipContent = '';
		if (!$mobile) {
			if ($showSidebar) {
				tooltipContent = $i18n.t('Close Sidebar');
			} else {
				tooltipContent = $i18n.t('Open Sidebar');
			}
		}
	}

	function canShowTooltip(): boolean {
		return !window.matchMedia('(hover: none)').matches;
	}

	const hoverTooltipOptions = {
		trigger: 'mouseenter',
		onShow: canShowTooltip
	};
</script>

<Tooltip
	content={tooltipContent}
	{placement}
	{interactive}
	touch={false}
	tippyOptions={hoverTooltipOptions}
>
	<slot />
</Tooltip>
