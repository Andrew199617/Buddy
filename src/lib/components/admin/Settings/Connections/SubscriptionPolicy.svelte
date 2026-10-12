<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getSubscriptionPolicy,
		saveSubscriptionPolicy,
		type SubscriptionPolicy
	} from '$lib/apis/subscriptions';
	import { getGroups } from '$lib/apis/groups';

	import Switch from '$lib/components/common/Switch.svelte';

	const i18n: any = getContext('i18n');

	let policy: SubscriptionPolicy | null = null;
	let groups: { id: string; name: string }[] = [];
	let saving = false;

	onMount(async () => {
		try {
			policy = await getSubscriptionPolicy(localStorage.token);
			groups = (await getGroups(localStorage.token)) ?? [];
		} catch (error) {
			toast.error(`${error}`);
		}
	});

	const save = async (changes: Partial<SubscriptionPolicy>) => {
		if (!policy || saving) return;
		saving = true;
		try {
			policy = await saveSubscriptionPolicy(localStorage.token, changes);
			toast.success($i18n.t('Saved'));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};

	const changeWhoMayConnect = (event: Event) => {
		const choice = (event.currentTarget as HTMLSelectElement)
			.value as SubscriptionPolicy['user_machines'];
		save({ user_machines: choice });
	};

	const toggleGroup = (groupId: string, checked: boolean) => {
		if (!policy) return;
		let ids = policy.machine_group_ids.filter((id) => id !== groupId);
		if (checked) {
			ids = [...ids, groupId];
		}
		save({ machine_group_ids: ids });
	};
</script>

{#if policy}
	<fieldset class="flex flex-col gap-3" disabled={saving}>
		<div class="flex flex-col gap-1">
			<label class="text-xs text-gray-600 dark:text-gray-400" for="subscription-user-machines">
				{$i18n.t('Who can connect computers and use AI plans')}
			</label>
			<select
				id="subscription-user-machines"
				class="w-full rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850"
				value={policy.user_machines}
				on:change={changeWhoMayConnect}
			>
				<option value="all">{$i18n.t('Everyone')}</option>
				<option value="groups">{$i18n.t('Selected groups')}</option>
				<option value="admins">{$i18n.t('Administrators only')}</option>
			</select>
			{#if policy.user_machines === 'groups'}
				<div class="mt-1 flex flex-col gap-1">
					{#each groups as group (group.id)}
						<label class="flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								checked={policy.machine_group_ids.includes(group.id)}
								on:change={(event) => toggleGroup(group.id, event.currentTarget.checked)}
							/>
							{group.name}
						</label>
					{:else}
						<p class="text-[0.6875rem] text-gray-500">{$i18n.t('No groups yet.')}</p>
					{/each}
				</div>
			{/if}
		</div>

		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Plan sign-in')}</div>
				<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'People sign in with their own Claude or ChatGPT plan. Turn this off before offering Buddy publicly: Anthropic does not allow third-party products to offer Claude plan sign-in without its approval.'
					)}
				</p>
			</div>
			<Switch
				state={policy.plan_sign_in}
				ariaLabel={$i18n.t('Plan sign-in')}
				on:change={(event) => save({ plan_sign_in: event.detail })}
			/>
		</div>

		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Personal API keys')}</div>
				<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('People use their own Anthropic or OpenAI API key, billed to them per use.')}
				</p>
			</div>
			<Switch
				state={policy.api_keys}
				ariaLabel={$i18n.t('Personal API keys')}
				on:change={(event) => save({ api_keys: event.detail })}
			/>
		</div>

		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Run on this server')}</div>
				<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'The instance owner may also run Claude and ChatGPT inside the Buddy server itself.'
					)}
				</p>
			</div>
			<Switch
				state={policy.server_machine}
				ariaLabel={$i18n.t('Run on this server')}
				on:change={(event) => save({ server_machine: event.detail })}
			/>
		</div>
	</fieldset>
{/if}
