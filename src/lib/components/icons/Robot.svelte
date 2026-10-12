<script lang="ts">
	export let className = 'w-4 h-4';
	export let strokeWidth = '1.5';
</script>

<svg
	xmlns="http://www.w3.org/2000/svg"
	fill="none"
	viewBox="0 0 24 24"
	stroke-width={strokeWidth}
	stroke="currentColor"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
	class={className}
>
	<path d="M12 3.75V6.75" />
	<rect x="4.5" y="6.75" width="15" height="12" rx="3" />
	<path d="M2.25 11.25V14.25M21.75 11.25V14.25" />
	<path d="M9 11.25V12.75M15 11.25V12.75" />
	<path d="M9.75 15.75H14.25" />
</svg>
