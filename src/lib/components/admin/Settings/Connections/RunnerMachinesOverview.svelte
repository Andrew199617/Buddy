<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteAnySubscriptionMachine,
		getAllSubscriptionMachines,
		type SubscriptionMachine
	} from '$lib/apis/subscriptions';

	const i18n: any = getContext('i18n');

	let machines: SubscriptionMachine[] | null = null;
	let confirmingId: string | null = null;
	let busy = false;

	onMount(async () => {
		try {
			machines = (await getAllSubscriptionMachines(localStorage.token)).machines;
		} catch (error) {
			toast.error(`${error}`);
		}
	});

	const lastSeen = (machine: SubscriptionMachine) => {
		if (machine.online) {
			return $i18n.t('Online');
		}
		if (!machine.last_seen_at) {
			return $i18n.t('Not connected yet');
		}
		const date = new Date(machine.last_seen_at * 1000);
		return $i18n.t('Last seen {{date}}', {
			date: date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
		});
	};

	const remove = async (machine: SubscriptionMachine) => {
		busy = true;
		try {
			machines = (await deleteAnySubscriptionMachine(localStorage.token, machine.id)).machines;
			toast.success($i18n.t('Removed {{name}}', { name: machine.name }));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
			confirmingId = null;
		}
	};
</script>

{#if machines}
	{#if machines.length === 0}
		<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">{$i18n.t('No computers connected yet.')}</p>
	{:else}
		<ul class="flex flex-col gap-1">
			{#each machines as machine (machine.id)}
				<li class="flex flex-col gap-1">
					<div class="flex items-center gap-2">
						<span
							class="size-2 shrink-0 rounded-full {machine.online ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-700'}"
							aria-hidden="true"
						></span>
						<div class="min-w-0 flex-1">
							<div class="truncate text-sm">{machine.name}</div>
							<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
								{machine.owner_name || $i18n.t('Unknown user')} ·
								{#if machine.kind === 'runner'}
									{lastSeen(machine)}
								{:else}
									{machine.url ?? $i18n.t('This server')}
								{/if}
							</div>
						</div>
						<button
							type="button"
							class="min-h-8 shrink-0 rounded-lg px-2 text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
							disabled={busy}
							on:click={() => (confirmingId = machine.id)}
						>
							{$i18n.t('Remove')}
						</button>
					</div>
					{#if confirmingId === machine.id}
						<div class="ml-4 flex flex-wrap items-center gap-2 rounded-lg bg-red-50 p-2 text-xs dark:bg-red-950/30" role="alert">
							<span class="min-w-0 flex-1">
								{$i18n.t('Remove {{name}} for {{owner}}? Its providers turn off and its app disconnects.', {
									name: machine.name,
									owner: machine.owner_name || $i18n.t('Unknown user')
								})}
							</span>
							<button
								type="button"
								class="min-h-8 rounded-lg px-3 font-medium text-red-700 hover:bg-red-100 dark:text-red-300"
								disabled={busy}
								on:click={() => remove(machine)}
							>
								{$i18n.t('Remove')}
							</button>
							<button type="button" class="min-h-8 rounded-lg px-3" disabled={busy} on:click={() => (confirmingId = null)}>
								{$i18n.t('Cancel')}
							</button>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
{/if}
