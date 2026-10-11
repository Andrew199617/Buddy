<script lang="ts">
	import SettingsSaveButton from './SettingsSaveButton.svelte';
	import { createEventDispatcher, getContext } from 'svelte';
	import InterfaceSettings from '$lib/components/common/InterfaceSettings.svelte';

	const dispatch = createEventDispatcher();
	const i18n: any = getContext('i18n');

	export let saveSettings: Function;
	export let personalSettingsValue: Record<string, any> = {};

	let interfaceSettings: any;

	const saveInterfaceSettings = async () => {
		await interfaceSettings.save();
	};
	let saveButton: SettingsSaveButton;
</script>

<form
	id="tab-interface"
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => saveButton.save()}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.interface.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<InterfaceSettings bind:this={interfaceSettings} {saveSettings} {personalSettingsValue} />
	</div>

	<div class="shrink-0 flex justify-end text-sm font-normal">
		<SettingsSaveButton
			bind:this={saveButton}
			type="submit"
			saveAction={saveInterfaceSettings}
			on:save={(event) => dispatch('save', event.detail)}
		/>
	</div>
</form>
