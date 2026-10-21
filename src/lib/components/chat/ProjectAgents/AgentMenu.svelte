<script lang="ts">
	// The composer's robot button. It picks one of the custom agents in the folder the
	// chat runs in: the exact folder its Run Target bar chose, or else the chat folder's
	// project folder, this person's own choice for the folder. Agents on a
	// Buddy app computer are read through the relay; Buddy reads This server itself.
	// It shows for a chat folder with a project folder it can read, and while the
	// chat has an agent so that it can be cleared.
	import { getContext } from 'svelte';

	import { CompanionError } from '$lib/apis/companion';
	import { RelayCompanionClient } from '$lib/apis/companion-relay';
	import { getFolderProject, getServerAgentFiles } from '$lib/apis/subscriptions';
	import {
		projectTargets,
		type ProjectTarget
	} from '$lib/components/chat/ProjectPanel/project-session';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Robot from '$lib/components/icons/Robot.svelte';
	import { projectFolderChanges } from '$lib/components/projectFolders/project-folder-changes';
	import { lastSegment } from '$lib/components/projectFolders/project-folders';

	import type { ChatAgent } from './chat-agent';
	import {
		agentReadProblem,
		agentsFromFiles,
		agentTarget,
		listProjectAgents,
		type AgentListing
	} from './list-agents';

	const i18n: any = getContext('i18n');

	export let folderId: string | null = null;
	// The exact folder the chat runs in, from its Run Target bar; it wins over the folder's.
	export let chatTarget: ProjectTarget | null = null;
	export let selectedAgent: ChatAgent | null = null;
	export let onSelect: (agent: ChatAgent | null) => void = () => {};
	export let preserveComposerFocus = false;
	export let onClose: () => void = () => {};
	// Whether the button shows, so the composer can place a divider before it.
	export let available = false;

	type AgentList =
		| { phase: 'loading' }
		| { phase: 'ready'; agents: ChatAgent[]; leftOut: number }
		| { phase: 'blocked'; message: string }
		| { phase: 'failed'; message: string };

	let dropdown: Dropdown;
	let show = false;
	let folderTarget: ProjectTarget | null = null;
	let targetFor: string | null = null;
	let targetRequest = 0;
	let list: AgentList = { phase: 'loading' };
	let listRequest = 0;

	$: target = chatTarget ?? folderTarget;
	// No button for a folder only on computers whose agents can never be read.
	$: available = target !== null || selectedAgent !== null;
	$: projectName = target ? lastSegment(target.path) : '';

	async function loadTarget(id: string | null) {
		const request = ++targetRequest;
		if (id !== targetFor) {
			// Another chat folder: no button until its project folder arrives.
			targetFor = id;
			folderTarget = null;
		}
		if (!id) return;
		try {
			const project = await getFolderProject(localStorage.token, id);
			if (request !== targetRequest) return;
			folderTarget = null;
			if (project) folderTarget = agentTarget(projectTargets(project));
		} catch {
			// Keeps what is shown; opening the menu loads it again.
		}
	}

	$: void loadTarget(folderId);
	// Saved from the folder page or its settings while this chat is open.
	$: if ($projectFolderChanges.folderId === folderId && $projectFolderChanges.count > 0) {
		void loadTarget(folderId);
	}

	function describeFailure(cause: unknown, computerName: string): string {
		// Buddy's own reason, such as a project folder that no longer exists.
		if (typeof cause === 'string') return cause;
		if (cause instanceof CompanionError && cause.status === 401) {
			return $i18n.t('Your Buddy sign-in ended. Sign in again, then try again.');
		}
		if (cause instanceof Error && cause.name === 'AbortError') {
			return $i18n.t('{{name}} did not answer in time. Try again.', { name: computerName });
		}
		if (cause instanceof TypeError) {
			return $i18n.t('Cannot reach Buddy. Check your connection and try again.');
		}
		if (cause instanceof Error) return cause.message;
		return $i18n.t('The request to {{name}} failed.', { name: computerName });
	}

	function blockedMessage(opened: ProjectTarget): string | null {
		const problem = agentReadProblem(opened);
		const name = opened.computer.name;
		if (problem === 'choose_again') {
			return $i18n.t('{{name}} was set up again. Choose the folder again.', { name });
		}
		if (problem === 'needs_app') {
			return $i18n.t('Reading agents here needs the Buddy app on {{name}}.', { name });
		}
		if (problem === 'offline') {
			return $i18n.t('{{name}} is offline. Open the Buddy app on it, then try again.', { name });
		}
		return null;
	}

	/** This server is read by Buddy itself; other computers through the relay. */
	async function readAgents(opened: ProjectTarget): Promise<AgentListing> {
		if (opened.computer.kind === 'server') {
			const machineId = opened.computer.machine_id;
			const reply = await getServerAgentFiles(localStorage.token, machineId, opened.path);
			return { status: 'ready', agents: agentsFromFiles(reply.files), leftOut: reply.left_out };
		}
		const client = new RelayCompanionClient(opened.computer.machine_id, localStorage.token);
		try {
			return await listProjectAgents(client, opened);
		} finally {
			// Ends this page's session on the computer.
			void client.disconnect().catch(() => {});
		}
	}

	/** Reads the agents again each time the menu opens, so new and changed files show up. */
	async function loadAgents() {
		const request = ++listRequest;
		list = { phase: 'loading' };
		if (!chatTarget) await loadTarget(folderId);
		if (request !== listRequest) return;

		const opened = chatTarget ?? folderTarget;
		if (!opened) {
			list = {
				phase: 'blocked',
				message: $i18n.t("This chat's folder has no project folder on your computers.")
			};
			return;
		}
		const blocked = blockedMessage(opened);
		if (blocked) {
			list = { phase: 'blocked', message: blocked };
			return;
		}

		try {
			const listing = await readAgents(opened);
			if (request !== listRequest) return;
			if (listing.status === 'not_shared') {
				list = {
					phase: 'blocked',
					message: $i18n.t(
						'To read its agents, share this folder or one that contains it in the Buddy app on {{computer}}.',
						{ computer: opened.computer.name }
					)
				};
				return;
			}
			list = { phase: 'ready', agents: listing.agents, leftOut: listing.leftOut };
		} catch (cause) {
			if (request !== listRequest) return;
			list = { phase: 'failed', message: describeFailure(cause, opened.computer.name) };
		}
	}

	function openChanged(open: boolean) {
		if (open) {
			void loadAgents();
			return;
		}
		// A read still under way is ignored when it finishes.
		listRequest++;
		onClose();
	}

	function choose(agent: ChatAgent | null) {
		onSelect(agent);
		dropdown?.close();
	}

	function isSelected(agent: ChatAgent): boolean {
		return selectedAgent?.path === agent.path;
	}

	let triggerLabel = '';
	$: if (selectedAgent) {
		triggerLabel = $i18n.t('Agent: {{name}}', { name: selectedAgent.name });
	} else {
		triggerLabel = $i18n.t('Choose an agent');
	}

	let triggerClass = '';
	$: if (selectedAgent) {
		triggerClass =
			'text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-600/10 border border-sky-200/40 dark:border-sky-500/20 pl-1.5 pr-2';
	} else {
		triggerClass =
			'bg-transparent text-gray-700 dark:text-white hover:bg-gray-100 dark:hover:bg-gray-800 size-[1.875rem] justify-center';
	}

	const rowClass =
		'flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left text-[0.8125rem] hover:bg-gray-50/60 focus-visible:bg-gray-50/60 dark:hover:bg-gray-800/60 dark:focus-visible:bg-gray-800/60';
</script>

{#if available}
	<Dropdown
		bind:this={dropdown}
		bind:show
		align="start"
		side="top"
		{preserveComposerFocus}
		composerMenu="agents"
		visualViewportAware={preserveComposerFocus}
		onOpenChange={openChanged}
	>
		<Tooltip content={triggerLabel} placement="top">
			<button
				type="button"
				id="agent-menu-button"
				class="flex h-[1.875rem] max-w-[10rem] shrink-0 items-center gap-1 rounded-full text-sm outline-hidden transition-colors focus:outline-hidden {triggerClass}"
				aria-label={triggerLabel}
			>
				<Robot className="size-4.5 shrink-0" strokeWidth="1.5" />
				{#if selectedAgent}
					<span class="truncate">{selectedAgent.name}</span>
				{/if}
			</button>
		</Tooltip>

		<div slot="content">
			<DropdownMenu className="w-72 max-w-[calc(100vw-2rem)]">
				<div class="flex flex-col">
					<div
						class="px-2 pb-1 pt-1.5 text-[0.625rem] uppercase tracking-wider text-gray-500 dark:text-gray-400"
					>
						{#if projectName}
							{$i18n.t('Agents in {{folder}}', { folder: projectName })}
						{:else}
							{$i18n.t('Agents')}
						{/if}
					</div>

					<button
						type="button"
						role="menuitemradio"
						aria-checked={selectedAgent === null}
						class={rowClass}
						on:click={() => choose(null)}
					>
						<span class="min-w-0 flex-1 truncate">{$i18n.t('No agent')}</span>
						{#if selectedAgent === null}
							<Check className="size-3.5 shrink-0" strokeWidth="2" />
						{/if}
					</button>

					{#if list.phase === 'loading'}
						<div class="flex items-center gap-2 px-2 py-2 text-xs text-gray-500" role="status">
							<Spinner className="size-3.5" />
							{$i18n.t('Reading agents…')}
						</div>
					{:else if list.phase === 'blocked'}
						<p class="px-2 py-2 text-xs text-gray-600 dark:text-gray-300">{list.message}</p>
					{:else if list.phase === 'failed'}
						<div class="flex flex-col gap-1 px-2 py-2 text-xs">
							<p role="alert" class="text-red-600 dark:text-red-400">{list.message}</p>
							<button
								type="button"
								class="self-start rounded-lg px-2 py-1 font-medium hover:bg-gray-100 dark:hover:bg-gray-800"
								on:click={() => void loadAgents()}
							>
								{$i18n.t('Try again')}
							</button>
						</div>
					{:else}
						<div class="max-h-64 overflow-y-auto overflow-x-hidden scrollbar-thin">
							{#each list.agents as agent (agent.path)}
								<button
									type="button"
									role="menuitemradio"
									aria-checked={isSelected(agent)}
									class={rowClass}
									title={agent.path}
									on:click={() => choose(agent)}
								>
									<span class="flex min-w-0 flex-1 flex-col">
										<span class="truncate">{agent.name}</span>
										<span class="truncate text-xs text-gray-500 dark:text-gray-400">
											{agent.description || agent.path}
										</span>
									</span>
									{#if isSelected(agent)}
										<Check className="size-3.5 shrink-0" strokeWidth="2" />
									{/if}
								</button>
							{/each}
						</div>
						{#if list.agents.length === 0}
							<p class="px-2 py-2 text-xs text-gray-600 dark:text-gray-300">
								{$i18n.t(
									'No agents yet. Add .agent.md files to .github/agents, or Claude agents to .claude/agents.'
								)}
							</p>
						{/if}
						{#if list.leftOut > 0}
							<p class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400">
								{$i18n.t('{{count}} more agent files were left out.', { count: list.leftOut })}
							</p>
						{/if}
					{/if}
				</div>
			</DropdownMenu>
		</div>
	</Dropdown>
{/if}
