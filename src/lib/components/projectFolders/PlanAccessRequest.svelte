<script lang="ts">
	// Asks, inside a chat folder's settings, to let a Chat-only plan use files
	// on its computer, so the person need not leave for Settings → Machines.
	import { getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { updateSubscriptionConfig, type FolderPlan } from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		fullAccessDescription,
		fullAccessWarning,
		readFilesDescription
	} from '$lib/components/admin/Settings/Connections/plan-access';

	const i18n: any = getContext('i18n');

	type FileAccess = 'read' | 'full';

	// Plans that are on, Chat only, and have a computer they can use.
	export let plans: FolderPlan[];
	// Loads the folder's plans again, after a change or a refusal.
	export let reload: () => Promise<void>;
	// Called once a plan may use files and the plans were loaded again.
	export let onAllowed: () => void = () => {};
	// Shown under the folder choices, because another plan already opens folders.
	export let compact = false;

	// The plan being asked about, by ID: a reload replaces the plan objects.
	let confirming: { planId: string; access: FileAccess } | null = null;
	let saving = false;
	let error = '';

	async function ask(plan: FolderPlan, access: FileAccess) {
		confirming = { planId: plan.id, access };
		error = '';
		await tick();
		// Cancel first, like the computer's own permission questions.
		document.getElementById(`plan-access-cancel-${plan.id}`)?.focus();
	}

	async function cancel() {
		if (!confirming) return;
		const { planId, access } = confirming;
		confirming = null;
		error = '';
		await tick();
		document.getElementById(`plan-access-${access}-${planId}`)?.focus();
	}

	function allowedMessage(plan: FolderPlan, access: FileAccess, computerName: string): string {
		if (access === 'full') {
			return $i18n.t('{{name}} now has full access on {{computer}}.', {
				name: plan.name,
				computer: computerName
			});
		}
		return $i18n.t('{{name}} can now read files on {{computer}}.', {
			name: plan.name,
			computer: computerName
		});
	}

	async function allow() {
		if (!confirming || saving) return;
		const { planId, access } = confirming;
		const plan = plans.find((candidate) => candidate.id === planId);
		const machine = plan?.machine;
		if (!plan || !machine) return;
		saving = true;
		error = '';
		try {
			// Only the access level: the computer must be the one the person saw.
			await updateSubscriptionConfig(
				localStorage.token,
				plan.id,
				{ access },
				machine.id,
				machine.revision
			);
		} catch (cause) {
			error = `${cause}`;
			saving = false;
			// The plan may have changed elsewhere; the next try confirms what it is now.
			await reload();
			return;
		}
		confirming = null;
		saving = false;
		toast.success(allowedMessage(plan, access, machine.name));
		await reload();
		onAllowed();
	}

	function question(plan: FolderPlan, access: FileAccess, computerName: string): string {
		if (access === 'full') {
			return $i18n.t('Give {{name}} full access on {{computer}}?', {
				name: plan.name,
				computer: computerName
			});
		}
		return $i18n.t('Let {{name}} read files on {{computer}}?', {
			name: plan.name,
			computer: computerName
		});
	}

	function description(plan: FolderPlan, access: FileAccess): string {
		if (access === 'full') return fullAccessDescription($i18n);
		return readFilesDescription($i18n, plan.id);
	}
</script>

<div
	class="flex flex-col gap-2 rounded-xl border border-gray-100 p-2.5 dark:border-gray-850"
	role="group"
	aria-labelledby="plan-access-heading"
>
	<div>
		{#if compact}
			<p id="plan-access-heading" class="text-xs text-gray-500">
				{$i18n.t('Plans that only chat')}
			</p>
			<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t(
					'Their chats here don’t use files. Allowing files changes the plan for all your chats, not just this folder.'
				)}
			</p>
		{:else}
			<p id="plan-access-heading" class="text-sm">{$i18n.t('Let a plan work with files')}</p>
			<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t(
					'Your plans are set to Chat only, so they can’t open a project folder. Allowing files changes the plan for all your chats, not just this folder.'
				)}
			</p>
		{/if}
	</div>

	<ul class="flex flex-col gap-2">
		{#each plans as plan (plan.id)}
			<li class="flex flex-col gap-1.5">
				<div class="text-sm">
					{plan.name}
					<span class="text-[0.6875rem] text-gray-500">
						· {plan.machine?.name} · {$i18n.t('Chat only')}</span
					>
				</div>

				{#if confirming && confirming.planId === plan.id && plan.machine}
					<div
						class="flex flex-col gap-1.5 rounded-lg bg-gray-50 p-2.5 dark:bg-gray-850"
						role="group"
						aria-labelledby="plan-access-question-{plan.id}"
					>
						<p id="plan-access-question-{plan.id}" class="text-sm font-medium">
							{question(plan, confirming.access, plan.machine.name)}
						</p>
						<p class="text-[0.6875rem] text-gray-600 dark:text-gray-300">
							{description(plan, confirming.access)}
						</p>
						{#if confirming.access === 'full'}
							<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">
								{fullAccessWarning($i18n)}
							</p>
						{/if}
						<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
							{$i18n.t(
								'This applies to all your {{name}} chats, and its replies running now stop. You can change it later in Settings → Machines.',
								{ name: plan.name }
							)}
						</p>
						{#if error}
							<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
						{/if}
						<div class="flex flex-col gap-1.5 md:flex-row md:justify-end">
							<button
								type="button"
								id="plan-access-cancel-{plan.id}"
								class="min-h-11 rounded-lg px-3 text-xs font-medium hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800 md:min-h-8"
								disabled={saving}
								on:click={cancel}
							>
								{$i18n.t('Cancel')}
							</button>
							<button
								type="button"
								class="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-black px-3 text-xs font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black md:min-h-8"
								disabled={saving}
								on:click={allow}
							>
								{$i18n.t('Allow')}
								{#if saving}<Spinner className="size-3.5" />{/if}
							</button>
						</div>
					</div>
				{:else}
					<div class="flex flex-col gap-1.5 md:flex-row">
						<button
							type="button"
							id="plan-access-read-{plan.id}"
							class="min-h-11 rounded-lg border border-gray-100 px-3 text-xs font-medium hover:bg-gray-100 disabled:opacity-50 dark:border-gray-850 dark:hover:bg-gray-850 md:min-h-8"
							disabled={saving}
							on:click={() => ask(plan, 'read')}
						>
							{$i18n.t('Allow reading files')}
						</button>
						<button
							type="button"
							id="plan-access-full-{plan.id}"
							class="min-h-11 rounded-lg border border-gray-100 px-3 text-xs font-medium hover:bg-gray-100 disabled:opacity-50 dark:border-gray-850 dark:hover:bg-gray-850 md:min-h-8"
							disabled={saving}
							on:click={() => ask(plan, 'full')}
						>
							{$i18n.t('Allow full access')}
						</button>
					</div>
				{/if}
			</li>
		{/each}
	</ul>
</div>
