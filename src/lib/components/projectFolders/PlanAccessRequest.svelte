<script lang="ts">
	// Asks, inside a chat folder's settings, to let a Chat-only plan use files
	// on its computer, so the person need not leave for Settings → Machines.
	import { getContext, onDestroy, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { updateSubscriptionConfig, type FolderPlan } from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		fullAccessDescription,
		fullAccessWarning,
		readFilesDescription
	} from '$lib/components/admin/Settings/Connections/plan-access';
	import { focusWasLost, type FileAccess } from './project-folders';

	const i18n: any = getContext('i18n');

	// Plans that are on, Chat only, and have a computer they can use.
	export let plans: FolderPlan[];
	// Loads the folder's plans again, after a change or a refusal.
	export let reload: () => Promise<void>;
	// Called once a plan may use files and the plans were loaded again.
	export let onAllowed: () => void = () => {};
	// Called when an Allow was refused and this request cannot show why: the plan
	// is gone, or the request was removed or hidden meanwhile.
	export let onRefused: (plan: FolderPlan, access: FileAccess, reason: string) => void = () => {};
	// Shown under the folder choices, because another plan already opens folders.
	export let compact = false;
	// Hidden for now, but kept so an Allow in progress stays busy.
	export let hidden = false;

	// The plan being asked about, by ID: a reload replaces the plan objects.
	let confirming: { planId: string; access: FileAccess } | null = null;
	let saving = false;
	let error = '';
	// A reload can replace this request with something else; its plans are then stale.
	let destroyed = false;

	onDestroy(() => {
		destroyed = true;
	});

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
			await refused(plan, access, `${cause}`);
			return;
		}
		// The question stays, busy, until the plans show the change.
		await reload();
		confirming = null;
		saving = false;
		toast.success(allowedMessage(plan, access, machine.name));
		onAllowed();
	}

	/** The plan may have changed elsewhere, so the plans load again before saying why. */
	async function refused(plan: FolderPlan, access: FileAccess, reason: string) {
		await reload();
		await tick();
		saving = false;
		const stillOffered = plans.some((candidate) => candidate.id === plan.id);
		if (!destroyed && !hidden && stillOffered) {
			// Still offered: the question stays, and the next try confirms the plan as it is now.
			error = reason;
			await tick();
			if (focusWasLost()) document.getElementById(`plan-access-allow-${plan.id}`)?.focus();
			return;
		}
		confirming = null;
		onRefused(plan, access, reason);
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

	/** Names only the plans listed: another plan that is on may already use files. */
	function chatOnlySummary(listed: FolderPlan[]): string {
		if (listed.length === 1) {
			return $i18n.t(
				'{{name}} is set to Chat only, so it can’t open a project folder. Allowing files changes it for all your chats, not just this folder.',
				{ name: listed[0].name }
			);
		}
		return $i18n.t(
			'These plans are set to Chat only, so they can’t open a project folder. Allowing files changes them for all your chats, not just this folder.'
		);
	}

	/** The question's details, read out with it when focus moves inside. */
	function questionDetailIds(planId: string, access: FileAccess): string {
		const ids = [`plan-access-what-${planId}`];
		if (access === 'full') ids.push(`plan-access-warning-${planId}`);
		ids.push(`plan-access-scope-${planId}`);
		return ids.join(' ');
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
				{chatOnlySummary(plans)}
			</p>
		{/if}
	</div>

	<ul class="flex flex-col gap-2">
		{#each plans as plan (plan.id)}
			<li class="flex flex-col gap-1.5">
				<div id="plan-access-plan-{plan.id}" class="text-sm">
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
						aria-describedby={questionDetailIds(plan.id, confirming.access)}
					>
						<p id="plan-access-question-{plan.id}" class="text-sm font-medium">
							{question(plan, confirming.access, plan.machine.name)}
						</p>
						<p
							id="plan-access-what-{plan.id}"
							class="text-[0.6875rem] text-gray-600 dark:text-gray-300"
						>
							{description(plan, confirming.access)}
						</p>
						{#if confirming.access === 'full'}
							<p
								id="plan-access-warning-{plan.id}"
								class="text-[0.6875rem] text-amber-700 dark:text-amber-400"
							>
								{fullAccessWarning($i18n)}
							</p>
						{/if}
						<p
							id="plan-access-scope-{plan.id}"
							class="text-[0.6875rem] text-gray-500 dark:text-gray-400"
						>
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
								id="plan-access-allow-{plan.id}"
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
							aria-describedby="plan-access-plan-{plan.id}"
							class="min-h-11 rounded-lg border border-gray-100 px-3 text-xs font-medium hover:bg-gray-100 disabled:opacity-50 dark:border-gray-850 dark:hover:bg-gray-850 md:min-h-8"
							disabled={saving}
							on:click={() => ask(plan, 'read')}
						>
							{$i18n.t('Allow reading files')}
						</button>
						<button
							type="button"
							id="plan-access-full-{plan.id}"
							aria-describedby="plan-access-plan-{plan.id}"
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
