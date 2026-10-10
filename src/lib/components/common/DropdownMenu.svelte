<script lang="ts">
	export let className = '';
	export let style = '';
</script>

<div
	class={`app-dropdown-menu rounded-xl! p-0.5! border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg ${className} [&>button]:flex [&>button]:w-full [&>button]:h-[1.6875rem] [&>button]:items-center [&>button]:gap-2 [&>button]:rounded-xl! [&>button]:px-2! [&>button]:text-[0.8125rem] [&>button:hover]:bg-gray-50/60 dark:[&>button:hover]:bg-gray-800/60 [&>a]:flex [&>a]:w-full [&>a]:h-[1.6875rem] [&>a]:items-center [&>a]:gap-2 [&>a]:rounded-xl! [&>a]:px-2! [&>a]:text-[0.8125rem] [&>a:hover]:bg-gray-50/60 dark:[&>a:hover]:bg-gray-800/60 [&>button_svg]:size-3.5 [&>a_svg]:size-3.5 [&>hr]:my-0.5 [&>hr]:mx-1`}
	{style}
>
	<slot />
</div>

<style>
	.app-dropdown-menu :global(.chat-menu-delete) {
		color: #b91c1c;
	}

	.app-dropdown-menu :global(.chat-menu-delete:hover),
	.app-dropdown-menu :global(.chat-menu-delete:focus-visible) {
		color: #991b1b;
		background-color: #fef2f2;
	}

	.app-dropdown-menu :global(.chat-menu-delete:focus-visible) {
		outline: 2px solid #b91c1c;
		outline-offset: -2px;
	}

	:global(.dark) .app-dropdown-menu :global(.chat-menu-delete) {
		color: #fca5a5;
	}

	:global(.dark) .app-dropdown-menu :global(.chat-menu-delete:hover),
	:global(.dark) .app-dropdown-menu :global(.chat-menu-delete:focus-visible) {
		color: #fecaca;
		background-color: #450a0a;
	}

	:global(.dark) .app-dropdown-menu :global(.chat-menu-delete:focus-visible) {
		outline-color: #fca5a5;
	}
</style>
