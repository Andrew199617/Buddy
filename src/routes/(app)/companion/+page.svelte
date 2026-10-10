<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { WEBUI_NAME, showSidebar, user } from '$lib/stores';
	import { getSubscriptionMachines, type SubscriptionMachine } from '$lib/apis/subscriptions';
	import SidebarControlIcon from '$lib/components/layout/SidebarControlIcon.svelte';
	import {
		CompanionClient,
		CompanionError,
		DEFAULT_COMPANION_ENDPOINT,
		type CompanionHost,
		type CompanionGrant,
		type CompanionDirectoryEntry,
		type CompanionWorkspace,
		type CompanionTerminal
	} from '$lib/apis/companion';

	let endpointInput = DEFAULT_COMPANION_ENDPOINT;
	let machines: SubscriptionMachine[] = [];
	let selectedMachineId = '';
	let machinesBusy = false;
	let machinesError = '';
	let hostEndpoint = '';
	let pairCode = '';
	let client: CompanionClient | null = null;
	let host: CompanionHost | null = null;
	let expiresAt: number | null = null;
	let grants: CompanionGrant[] = [];
	let activeGrantId = '';
	let directoryPath = '';
	let entries: CompanionDirectoryEntry[] = [];
	let workspace: CompanionWorkspace | null = null;
	let filePath: string | null = null;
	let fileContent = '';
	let savedFileContent = '';
	let fileWriteAccepted = false;
	let terminal: CompanionTerminal | null = null;
	let executable = '';
	let argumentsJson = '[]';
	let executionRiskAccepted = false;
	let error = '';
	let notice = '';
	let connectionBusy = false;
	let browseBusy = false;
	let workspaceBusy = false;
	let fileBusy = false;
	let terminalBusy = false;
	let commandUnconfirmed = false;
	let stoppingTerminal = false;
	let stopUnconfirmed = false;
	let disposed = false;
	let connectionGeneration = 0;
	let browseGeneration = 0;
	let fileGeneration = 0;
	let terminalGeneration = 0;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let expiryTimer: ReturnType<typeof setTimeout> | null = null;
	let pollFailures = 0;
	let pollErrorMessage = '';
	const pendingClients = new Set<CompanionClient>();

	$: activeGrant = grants.find((grant) => grant.id === activeGrantId) ?? null;
	$: fileDirty = filePath !== null && fileContent !== savedFileContent;
	$: terminalOutput = terminal?.output.slice(-262144) ?? '';

	function isCurrent(candidate: CompanionClient, generation: number): boolean {
		return !disposed && generation === connectionGeneration && client === candidate;
	}

	function isCurrentTerminal(
		candidate: CompanionClient,
		generation: number,
		requestGeneration: number,
		terminalId: string
	): boolean {
		return (
			isCurrent(candidate, generation) &&
			requestGeneration === terminalGeneration &&
			terminal?.id === terminalId &&
			terminal.workspaceId === workspace?.id
		);
	}

	function validateTerminalStatus(
		updated: CompanionTerminal,
		terminalId: string,
		workspaceId: string | undefined
	) {
		if (
			updated?.id !== terminalId ||
			updated.workspaceId !== workspaceId ||
			!['ready', 'running', 'exited', 'closed'].includes(updated.status) ||
			typeof updated.output !== 'string' ||
			(updated.exitCode !== null && !Number.isInteger(updated.exitCode))
		) {
			throw new CompanionError('The Runner did not confirm this command session status.', 502);
		}
	}

	function stopPolling() {
		if (pollTimer !== null) clearTimeout(pollTimer);
		pollTimer = null;
	}

	function clearFile() {
		fileGeneration += 1;
		filePath = null;
		fileContent = '';
		savedFileContent = '';
		fileWriteAccepted = false;
		fileBusy = false;
	}

	function clearTerminal() {
		terminalGeneration += 1;
		stopPolling();
		terminal = null;
		terminalBusy = false;
		commandUnconfirmed = false;
		stoppingTerminal = false;
		stopUnconfirmed = false;
		pollFailures = 0;
		pollErrorMessage = '';
		executionRiskAccepted = false;
	}

	function resetConnection(): CompanionClient | null {
		const previous = client;
		connectionGeneration += 1;
		browseGeneration += 1;
		if (expiryTimer !== null) clearTimeout(expiryTimer);
		expiryTimer = null;
		for (const pending of pendingClients) void pending.disconnect().catch(() => {});
		pendingClients.clear();
		client = null;
		host = null;
		hostEndpoint = '';
		expiresAt = null;
		grants = [];
		activeGrantId = '';
		directoryPath = '';
		entries = [];
		workspace = null;
		notice = '';
		connectionBusy = false;
		browseBusy = false;
		workspaceBusy = false;
		pairCode = '';
		clearFile();
		clearTerminal();
		return previous;
	}

	function scheduleSessionExpiry(candidate: CompanionClient, generation: number, expires: number) {
		if (expiryTimer !== null) clearTimeout(expiryTimer);
		expiryTimer = setTimeout(
			() => {
				expiryTimer = null;
				if (!isCurrent(candidate, generation)) return;
				const previous = resetConnection();
				void previous?.disconnect().catch(() => {});
				error = 'This host session expired. Inspect the computer and pair with a fresh code.';
			},
			Math.max(0, expires - Date.now())
		);
	}

	function describeError(cause: unknown): string {
		if (cause instanceof Error && cause.name === 'AbortError') {
			return 'The computer request timed out or was cancelled.';
		}
		if (cause instanceof TypeError) {
			return 'Cannot reach this Runner. Check its browser address, that it is running, and its allowed Buddy origin.';
		}
		if (cause instanceof Error) return cause.message;
		return 'The Runner project request failed.';
	}

	function reportError(cause: unknown, generation: number) {
		if (disposed || generation !== connectionGeneration) return;
		error = describeError(cause);
		if (cause instanceof CompanionError && (cause.status === 401 || cause.hostChanged)) {
			const previous = resetConnection();
			void previous?.disconnect().catch(() => {});
			if (cause.status === 401) {
				error =
					'This project session is no longer authorized. Inspect the computer and pair with a fresh code.';
			}
		}
	}

	async function loadMachines() {
		if ($user?.role !== 'admin') return;
		machinesBusy = true;
		try {
			const response = await getSubscriptionMachines(localStorage.token);
			if (disposed || $user?.role !== 'admin') return;
			machines = response.machines;
		} catch {
			if (!disposed)
				machinesError =
					'Saved computer addresses are unavailable. Enter a Runner browser address below.';
		} finally {
			if (!disposed) machinesBusy = false;
		}
	}

	function selectComputer(event: Event) {
		selectedMachineId = (event.currentTarget as HTMLSelectElement).value;
		const previous = resetConnection();
		void previous?.disconnect().catch(() => {});
		error = '';
		notice = '';
		if (!selectedMachineId) {
			endpointInput = DEFAULT_COMPANION_ENDPOINT;
			return;
		}
		const selected = machines.find((machine) => machine.id === selectedMachineId);
		endpointInput = selected?.browser_url ?? '';
		if (!endpointInput) {
			notice =
				'This computer has no browser address. Enter an explicitly configured Runner address; its backend address is not used.';
		}
	}

	function changeEndpoint(event: Event) {
		endpointInput = (event.currentTarget as HTMLInputElement).value;
		selectedMachineId = '';
		const previous = resetConnection();
		pairCode = '';
		error = '';
		notice = '';
		void previous?.disconnect().catch(() => {});
	}

	async function inspectHost() {
		error = '';
		notice = '';
		const previous = resetConnection();
		void previous?.disconnect().catch(() => {});
		const generation = connectionGeneration;
		connectionBusy = true;
		let candidate: CompanionClient | null = null;
		try {
			candidate = new CompanionClient(endpointInput);
			pendingClients.add(candidate);
			const identity = await candidate.getHost();
			if (disposed || generation !== connectionGeneration) return;
			host = identity;
			hostEndpoint = candidate.endpoint;
		} catch (cause) {
			reportError(cause, generation);
		} finally {
			if (candidate) pendingClients.delete(candidate);
			void candidate?.disconnect().catch(() => {});
			if (!disposed && generation === connectionGeneration) connectionBusy = false;
		}
	}

	async function pairHost() {
		const code = pairCode.trim();
		if (!code || !host) return;
		error = '';
		notice = '';
		const inspectedHost = host;
		const inspectedEndpoint = hostEndpoint;
		const previous = resetConnection();
		void previous?.disconnect().catch(() => {});
		const generation = connectionGeneration;
		connectionBusy = true;
		let candidate: CompanionClient | null = null;
		try {
			candidate = new CompanionClient(endpointInput);
			pendingClients.add(candidate);
			const identity = await candidate.getHost();
			if (disposed || generation !== connectionGeneration) return;
			if (
				inspectedHost &&
				inspectedEndpoint === candidate.endpoint &&
				inspectedHost.id !== identity.id
			) {
				throw new Error(
					'The execution host changed. Inspect its identity and use its current pairing code.'
				);
			}
			const paired = await candidate.pair(code);
			if (disposed || generation !== connectionGeneration) return;
			if (paired.host.id !== identity.id) {
				throw new Error(
					'The host identity changed during pairing. Inspect the host and try again.'
				);
			}
			client = candidate;
			host = paired.host;
			hostEndpoint = candidate.endpoint;
			expiresAt = paired.expiresAt;
			scheduleSessionExpiry(candidate, generation, paired.expiresAt);
			pairCode = '';
			const approvedGrants = await candidate.getGrants();
			if (!isCurrent(candidate, generation)) return;
			grants = approvedGrants;
			const firstGrant = approvedGrants.find((grant) => grant.capabilities.read);
			if (firstGrant) {
				activeGrantId = firstGrant.id;
				await browseDirectory('');
			}
			if (!isCurrent(candidate, generation)) return;
			notice = 'Paired for this page session. Closing or disconnecting clears the credential.';
		} catch (cause) {
			reportError(cause, generation);
			if (generation === connectionGeneration && candidate === client) resetConnection();
		} finally {
			if (candidate) {
				pendingClients.delete(candidate);
				if (candidate !== client) void candidate.disconnect().catch(() => {});
			}
			if (!disposed && generation === connectionGeneration) connectionBusy = false;
		}
	}

	async function disconnectHost() {
		error = '';
		notice = '';
		const previous = resetConnection();
		const generation = connectionGeneration;
		pairCode = '';
		try {
			await previous?.disconnect();
			if (!disposed && generation === connectionGeneration)
				notice = 'Disconnected. The host session and its commands were revoked.';
		} catch {
			if (!disposed && generation === connectionGeneration)
				error =
					'The local credential was cleared, but the host could not confirm revocation. Its session will expire automatically.';
		}
	}

	async function selectGrant(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		const selectedId = select.value;
		select.value = activeGrantId;
		const candidate = client;
		const generation = connectionGeneration;
		if (!candidate || workspaceBusy || terminalBusy) return;
		workspaceBusy = true;
		try {
			if (!(await stopTerminalBeforeChange()) || !isCurrent(candidate, generation)) return;
			clearTerminal();
			clearFile();
			workspace = null;
			activeGrantId = selectedId;
			entries = [];
			directoryPath = '';
			error = '';
			notice = '';
			await browseDirectory('');
		} finally {
			if (isCurrent(candidate, generation)) workspaceBusy = false;
		}
	}

	async function browseDirectory(path: string) {
		const candidate = client;
		const grantId = activeGrantId;
		if (!candidate || !grantId) return;
		const generation = connectionGeneration;
		const requestGeneration = ++browseGeneration;
		browseBusy = true;
		error = '';
		clearFile();
		try {
			const directory = await candidate.getDirectory(grantId, path);
			if (!isCurrent(candidate, generation) || requestGeneration !== browseGeneration) return;
			directoryPath = directory.path;
			entries = directory.entries;
		} catch (cause) {
			if (requestGeneration === browseGeneration) reportError(cause, generation);
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === browseGeneration)
				browseBusy = false;
		}
	}

	function parentDirectory() {
		const parts = directoryPath.split('/').filter(Boolean);
		parts.pop();
		void browseDirectory(parts.join('/'));
	}

	async function selectWorkspace() {
		const candidate = client;
		const grantId = activeGrantId;
		const path = directoryPath;
		if (!candidate || !grantId || workspaceBusy || terminalBusy) return;
		const generation = connectionGeneration;
		const requestGeneration = browseGeneration;
		workspaceBusy = true;
		error = '';
		try {
			if (!(await stopTerminalBeforeChange())) return;
			if (!isCurrent(candidate, generation) || requestGeneration !== browseGeneration) return;
			clearTerminal();
			const selected = await candidate.createWorkspace(grantId, path);
			if (!isCurrent(candidate, generation) || requestGeneration !== browseGeneration) return;
			executionRiskAccepted = false;
			workspace = selected;
			notice = `Selected ${selected.name} on ${host?.name ?? 'the execution host'}.`;
		} catch (cause) {
			reportError(cause, generation);
		} finally {
			if (isCurrent(candidate, generation)) workspaceBusy = false;
		}
	}

	async function openFile(path: string) {
		const candidate = client;
		if (!candidate || !activeGrantId) return;
		const generation = connectionGeneration;
		clearFile();
		const requestGeneration = fileGeneration;
		fileBusy = true;
		error = '';
		try {
			const file = await candidate.getFile(activeGrantId, path);
			if (!isCurrent(candidate, generation) || requestGeneration !== fileGeneration) return;
			filePath = file.path;
			fileContent = file.content;
			savedFileContent = file.content;
		} catch (cause) {
			if (requestGeneration === fileGeneration) reportError(cause, generation);
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === fileGeneration)
				fileBusy = false;
		}
	}

	async function saveFile() {
		const candidate = client;
		const path = filePath;
		const content = fileContent;
		if (!candidate || path === null || !activeGrant?.capabilities.write || !fileWriteAccepted)
			return;
		const generation = connectionGeneration;
		const requestGeneration = fileGeneration;
		fileBusy = true;
		error = '';
		try {
			await candidate.putFile(activeGrantId, path, content);
			if (!isCurrent(candidate, generation) || requestGeneration !== fileGeneration) return;
			savedFileContent = content;
			notice = `Saved ${path} on ${host?.name ?? 'the execution host'}.`;
		} catch (cause) {
			reportError(cause, generation);
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === fileGeneration)
				fileBusy = false;
		}
	}

	async function createCommandSession() {
		const candidate = client;
		const selectedWorkspace = workspace;
		if (
			!candidate ||
			!selectedWorkspace ||
			!activeGrant?.capabilities.execute ||
			!executionRiskAccepted ||
			workspaceBusy ||
			terminalBusy ||
			stoppingTerminal ||
			stopUnconfirmed
		)
			return;
		const generation = connectionGeneration;
		const requestGeneration = ++terminalGeneration;
		terminalBusy = true;
		error = '';
		try {
			const created = await candidate.createTerminal(selectedWorkspace.id);
			if (
				!isCurrent(candidate, generation) ||
				requestGeneration !== terminalGeneration ||
				workspace?.id !== selectedWorkspace.id
			) {
				void candidate.deleteTerminal(created.id).catch(() => {});
				return;
			}
			terminal = created;
		} catch (cause) {
			reportError(cause, generation);
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === terminalGeneration)
				terminalBusy = false;
		}
	}

	function schedulePoll(
		candidate: CompanionClient,
		generation: number,
		requestGeneration: number,
		terminalId: string,
		delay = 750
	) {
		stopPolling();
		if (
			!isCurrentTerminal(candidate, generation, requestGeneration, terminalId) ||
			(terminal?.status !== 'running' && !commandUnconfirmed) ||
			stoppingTerminal ||
			stopUnconfirmed
		)
			return;
		pollTimer = setTimeout(() => {
			pollTimer = null;
			void pollTerminal(candidate, generation, requestGeneration, terminalId);
		}, delay);
	}

	async function pollTerminal(
		candidate: CompanionClient,
		generation: number,
		requestGeneration: number,
		terminalId: string
	) {
		if (!isCurrentTerminal(candidate, generation, requestGeneration, terminalId)) return;
		try {
			const updated = await candidate.getTerminal(terminalId);
			if (!isCurrentTerminal(candidate, generation, requestGeneration, terminalId)) return;
			validateTerminalStatus(updated, terminalId, workspace?.id);
			terminal = updated;
			if (commandUnconfirmed && (updated.status === 'ready' || updated.status === 'exited')) {
				pollFailures = 0;
				pollErrorMessage =
					'The command request remains unconfirmed. A ready or finished terminal cannot prove whether it ran. Run is disabled; use Stop before creating a new session.';
				error = pollErrorMessage;
				schedulePoll(candidate, generation, requestGeneration, terminalId);
				return;
			}
			commandUnconfirmed = false;
			pollFailures = 0;
			if (error === pollErrorMessage) error = '';
			pollErrorMessage = '';
			schedulePoll(candidate, generation, requestGeneration, terminalId);
		} catch (cause) {
			if (!isCurrentTerminal(candidate, generation, requestGeneration, terminalId)) return;
			reportError(cause, generation);
			if (!isCurrentTerminal(candidate, generation, requestGeneration, terminalId)) return;
			if (commandUnconfirmed) {
				pollErrorMessage =
					'Command acceptance is still unconfirmed. Run is disabled; check its status, retry Stop or disconnect.';
				error = pollErrorMessage;
			}
			const transient =
				!(cause instanceof CompanionError) ||
				cause.status >= 500 ||
				cause.status === 408 ||
				cause.status === 429;
			if (transient && isCurrent(candidate, generation) && !stoppingTerminal && !stopUnconfirmed) {
				pollFailures = Math.min(pollFailures + 1, 5);
				if (!commandUnconfirmed) {
					pollErrorMessage =
						'Command status is temporarily unavailable. Retrying; Stop and disconnect remain available.';
				}
				error = pollErrorMessage;
				schedulePoll(
					candidate,
					generation,
					requestGeneration,
					terminalId,
					Math.min(10000, 750 * 2 ** pollFailures)
				);
			}
		}
	}

	async function checkCommandStatus() {
		const candidate = client;
		const selectedTerminal = terminal;
		if (
			!candidate ||
			!selectedTerminal ||
			!commandUnconfirmed ||
			workspaceBusy ||
			terminalBusy ||
			stoppingTerminal ||
			stopUnconfirmed
		)
			return;
		const generation = connectionGeneration;
		const requestGeneration = ++terminalGeneration;
		terminalBusy = true;
		try {
			stopPolling();
			await pollTerminal(candidate, generation, requestGeneration, selectedTerminal.id);
		} finally {
			if (isCurrentTerminal(candidate, generation, requestGeneration, selectedTerminal.id))
				terminalBusy = false;
		}
	}

	async function runCommand() {
		const candidate = client;
		const selectedTerminal = terminal;
		const selectedWorkspace = workspace;
		if (
			!candidate ||
			!selectedTerminal ||
			!selectedWorkspace ||
			selectedTerminal.workspaceId !== selectedWorkspace.id ||
			selectedTerminal.status === 'running' ||
			selectedTerminal.status === 'closed' ||
			!executionRiskAccepted ||
			commandUnconfirmed ||
			workspaceBusy ||
			terminalBusy ||
			stoppingTerminal ||
			stopUnconfirmed
		)
			return;
		const generation = connectionGeneration;
		const requestGeneration = ++terminalGeneration;
		stopPolling();
		error = '';
		terminalBusy = true;
		let commandSubmitted = false;
		try {
			const args: unknown = JSON.parse(argumentsJson);
			if (!Array.isArray(args) || !args.every((argument) => typeof argument === 'string')) {
				throw new Error('Arguments must be a JSON array of strings, for example ["--version"].');
			}
			if (!executable.trim()) throw new Error('Enter an executable name or path.');
			commandSubmitted = true;
			const updated = await candidate.runCommand(selectedTerminal.id, executable.trim(), args);
			if (
				!isCurrent(candidate, generation) ||
				requestGeneration !== terminalGeneration ||
				workspace?.id !== selectedWorkspace.id
			)
				return;
			validateTerminalStatus(updated, selectedTerminal.id, selectedWorkspace.id);
			terminal = updated;
			schedulePoll(candidate, generation, requestGeneration, selectedTerminal.id);
		} catch (cause) {
			if (!isCurrentTerminal(candidate, generation, requestGeneration, selectedTerminal.id)) return;
			reportError(cause, generation);
			const uncertain = !(cause instanceof CompanionError) || cause.status >= 500;
			if (
				commandSubmitted &&
				uncertain &&
				isCurrentTerminal(candidate, generation, requestGeneration, selectedTerminal.id) &&
				!stoppingTerminal &&
				!stopUnconfirmed
			) {
				commandUnconfirmed = true;
				pollErrorMessage =
					'The command request was not confirmed. Checking its status; Stop and disconnect remain available.';
				error = pollErrorMessage;
				await pollTerminal(candidate, generation, requestGeneration, selectedTerminal.id);
			}
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === terminalGeneration)
				terminalBusy = false;
		}
	}

	async function stopTerminalBeforeChange(): Promise<boolean> {
		const candidate = client;
		const selectedTerminal = terminal;
		if (!selectedTerminal) return true;
		if (!candidate || stoppingTerminal) return false;
		const generation = connectionGeneration;
		const requestGeneration = terminalGeneration;
		stopPolling();
		stoppingTerminal = true;
		terminalBusy = true;
		error = '';
		try {
			await candidate.deleteTerminal(selectedTerminal.id);
			if (!isCurrent(candidate, generation) || requestGeneration !== terminalGeneration)
				return false;
			clearTerminal();
			return true;
		} catch (cause) {
			if (isCurrent(candidate, generation) && requestGeneration === terminalGeneration) {
				stopUnconfirmed = true;
				reportError(cause, generation);
				if (isCurrent(candidate, generation)) {
					error = `Stop was not confirmed. The command may still be running. Retry Stop or disconnect to revoke this session. ${describeError(cause)}`;
				}
			}
			return false;
		} finally {
			if (isCurrent(candidate, generation) && requestGeneration === terminalGeneration) {
				stoppingTerminal = false;
				terminalBusy = false;
			}
		}
	}

	async function stopCommandSession() {
		const candidate = client;
		const generation = connectionGeneration;
		if (await stopTerminalBeforeChange()) {
			if (candidate && isCurrent(candidate, generation))
				notice = 'The command session was stopped and removed.';
		}
	}

	onMount(() => {
		void loadMachines();
	});

	onDestroy(() => {
		disposed = true;
		const previous = resetConnection();
		void previous?.disconnect().catch(() => {});
		for (const pending of pendingClients) void pending.disconnect().catch(() => {});
		pendingClients.clear();
	});
</script>

<svelte:head>
	<!-- Preserve the Open WebUI browser-title identifier required by LICENSE. -->
	<title>Projects / {$WEBUI_NAME}</title>
</svelte:head>

<div class="companion-page">
	<header class="page-heading">
		{#if !$showSidebar}
			<button
				type="button"
				id="sidebar-toggle-button"
				class="buddy-circle-button no-drag sidebar-toggle"
				aria-label="Open Sidebar"
				aria-controls="sidebar"
				aria-expanded={$showSidebar}
				on:click={() => showSidebar.set(true)}
			>
				<SidebarControlIcon />
			</button>
		{/if}
		<div>
			<p class="eyebrow">Buddy Runner</p>
			<h1>Your projects, on your selected computer</h1>
			<p class="muted">
				Connect this browser to a computer that runs Buddy Runner. Select only folders approved on
				that computer.
			</p>
		</div>
	</header>

	{#if error}
		<div class="message error" role="alert">{error}</div>
	{/if}
	{#if notice}
		<div class="message notice" role="status">{notice}</div>
	{/if}

	<section class="panel connection-panel" aria-labelledby="connection-heading">
		<div class="section-heading">
			<h2 id="connection-heading">Computer</h2>
			<span class:connected={client !== null} class="status"
				>{client ? 'Paired' : 'Disconnected'}</span
			>
		</div>
		{#if $user?.role === 'admin'}
			<label for="runner-computer">Saved computer</label>
			<select
				id="runner-computer"
				value={selectedMachineId}
				on:change={selectComputer}
				disabled={machinesBusy}
			>
				<option value="">Enter a browser address</option>
				{#each machines as machine (machine.id)}
					<option value={machine.id}
						>{machine.name}{machine.browser_url ? '' : ' (address needed)'}</option
					>
				{/each}
			</select>
			{#if machinesBusy}<p class="muted hint">Loading saved computers…</p>{/if}
			{#if machinesError}<p class="muted hint">{machinesError}</p>{/if}
		{/if}
		<label for="companion-endpoint">Host address</label>
		<div class="field-row">
			<input
				id="companion-endpoint"
				type="url"
				value={endpointInput}
				on:input={changeEndpoint}
				spellcheck="false"
				autocapitalize="none"
				placeholder={DEFAULT_COMPANION_ENDPOINT}
			/>
			<button on:click={inspectHost} disabled={connectionBusy || client !== null}
				>Inspect host</button
			>
		</div>
		<p class="muted hint">
			Buddy Runner uses 8765 on the chosen computer. On a phone, loopback points to the phone. Enter
			the computer’s explicitly configured HTTPS browser address; remote access must be configured
			separately. A backend address such as host.docker.internal is not a browser address.
		</p>
		<p class="muted hint">
			Claude and ChatGPT commands run on the machine selected in Subscriptions. Selecting a project
			here does not change that setting, provider sign-in, or directory permissions. Codex Read
			files can read files available to that machine’s OS account; these scoped file permissions are
			separate.
		</p>
		{#if host}
			<div class="host-identity">
				<strong>{host.name}</strong>
				<span>{host.platform} · Buddy Runner {host.version}</span>
				<code>Host ID: {host.id}</code>
				<code>{hostEndpoint}</code>
			</div>
		{/if}
		{#if client}
			<div class="field-row session-row">
				<p class="muted">
					Session expires {expiresAt ? new Date(expiresAt).toLocaleString() : 'when revoked'}.
				</p>
				<button on:click={disconnectHost}>Disconnect and revoke</button>
			</div>
		{:else}
			<form class="pair-form" on:submit|preventDefault={pairHost}>
				<label for="companion-code">One-time pairing code from this host</label>
				<div class="field-row">
					<input
						id="companion-code"
						type="password"
						bind:value={pairCode}
						autocomplete="off"
						autocapitalize="none"
						spellcheck="false"
						placeholder="Enter the code shown by Buddy Runner"
					/>
					<button
						type="submit"
						class="primary"
						disabled={connectionBusy || !host || !pairCode.trim()}
						>{connectionBusy ? 'Connecting…' : 'Pair host'}</button
					>
				</div>
			</form>
			<p class="muted hint">
				Pairing uses a short-lived project credential held in memory. Buddy account tokens and
				provider credentials are never sent by this page. Model sign-in grants no project access.
			</p>
		{/if}
	</section>

	<div class="workspace-grid">
		<section class="panel project-panel" aria-labelledby="projects-heading">
			<h2 id="projects-heading">Project directory</h2>
			{#if client && host}
				<div class="host-context">
					<strong>{host.name}</strong><code>{host.id} · {hostEndpoint}</code>
				</div>
				{#if grants.length === 0}
					<p class="empty-state">
						This host has no approved directory grants. Configure a grant on the execution computer,
						then pair again.
					</p>
				{:else}
					<label for="companion-grant">Host-approved folder</label>
					<select
						id="companion-grant"
						value={activeGrantId}
						on:change={selectGrant}
						disabled={workspaceBusy || terminalBusy || stoppingTerminal}
					>
						<option value="" disabled>Select a grant</option>
						{#each grants as grant (grant.id)}
							<option value={grant.id} disabled={!grant.capabilities.read}
								>{grant.name}{grant.capabilities.read ? '' : ' (read disabled)'}</option
							>
						{/each}
					</select>
					{#if activeGrant}
						<p class="path-label">Grant root <code>{activeGrant.path}</code></p>
						<div class="capabilities">
							<span>{activeGrant.capabilities.read ? 'Read allowed' : 'Read disabled'}</span>
							<span>{activeGrant.capabilities.write ? 'Write allowed' : 'Write disabled'}</span>
							<span
								>{activeGrant.capabilities.execute ? 'Execute allowed' : 'Execute disabled'}</span
							>
						</div>
						<div class="directory-toolbar">
							<button
								on:click={parentDirectory}
								disabled={!directoryPath || browseBusy || workspaceBusy || terminalBusy}>Up</button
							>
							<code title={directoryPath || '/'}>{directoryPath || '/ (grant root)'}</code>
							<button
								on:click={() => browseDirectory(directoryPath)}
								disabled={browseBusy || workspaceBusy || terminalBusy}>Refresh</button
							>
						</div>
						<div class="directory-list" aria-busy={browseBusy}>
							{#if browseBusy}
								<p class="empty-state">Reading directory…</p>
							{:else if entries.length === 0}
								<p class="empty-state">No accessible entries in this directory.</p>
							{:else}
								{#each entries as entry (entry.path)}
									<button
										class="entry"
										disabled={workspaceBusy || fileBusy || terminalBusy}
										on:click={() =>
											entry.type === 'directory'
												? browseDirectory(entry.path)
												: openFile(entry.path)}
									>
										<span class="entry-kind">{entry.type === 'directory' ? 'Folder' : 'File'}</span>
										<span>{entry.name}</span>
									</button>
								{/each}
							{/if}
						</div>
						<button
							class="primary select-project"
							on:click={selectWorkspace}
							disabled={browseBusy || workspaceBusy || terminalBusy}
							>{workspaceBusy ? 'Selecting…' : 'Use this directory as project'}</button
						>
					{/if}
				{/if}
				{#if workspace}
					<div class="selected-project">
						<strong>Selected project: {workspace.name}</strong><code>{workspace.path}</code><span
							>Execution host ID: {workspace.hostId}</span
						>
					</div>
				{/if}
			{:else}
				<p class="empty-state">
					Pair with an execution host to browse its approved folders and select a real project
					directory.
				</p>
			{/if}
			<p class="muted hint">
				Buddy chat folders organize conversations. They grant no filesystem access. A supplied path
				alone grants nothing.
			</p>
		</section>

		<section class="panel file-panel" aria-labelledby="file-heading">
			<div class="section-heading">
				<h2 id="file-heading">Text file</h2>
				{#if fileDirty}<span class="status">Unsaved changes</span>{/if}
			</div>
			{#if client && host}
				<div class="host-context">
					<strong>{host.name}</strong><code>{host.id} · {hostEndpoint}</code>
				</div>
			{/if}
			{#if filePath !== null}
				<p class="path-label"><code>{filePath}</code></p>
				{#if activeGrant?.capabilities.write}
					<label class="risk-checkbox"
						><input type="checkbox" bind:checked={fileWriteAccepted} disabled={fileBusy} />
						<span>Allow editing this file on {host?.name}.</span>
					</label>
				{/if}
				<label for="companion-file-content" class="muted"
					>{activeGrant?.capabilities.write && fileWriteAccepted
						? 'Edit existing text file'
						: 'Read-only text file'}</label
				>
				<textarea
					id="companion-file-content"
					class="file-editor"
					bind:value={fileContent}
					readonly={!activeGrant?.capabilities.write || !fileWriteAccepted}
					spellcheck="false"
				></textarea>
				{#if activeGrant?.capabilities.write}
					<button
						class="primary"
						on:click={saveFile}
						disabled={!fileDirty || fileBusy || !fileWriteAccepted}
						>{fileBusy ? 'Saving…' : 'Save to execution host'}</button
					>
				{/if}
				<p class="muted hint">
					Opening another file, browsing another directory, or disconnecting discards unsaved edits.
				</p>
			{:else}
				<p class="empty-state">
					{fileBusy ? 'Reading file…' : 'Choose an existing text file from the directory browser.'}
				</p>
			{/if}
		</section>
	</div>

	<section class="panel terminal-panel" aria-labelledby="terminal-heading">
		<div class="section-heading">
			<h2 id="terminal-heading">Command session</h2>
			{#if terminal}<span class="status"
					>{#if stoppingTerminal}stopping{:else if stopUnconfirmed}stop unconfirmed{:else if commandUnconfirmed}status
						unconfirmed{:else}{terminal.status}{/if}{terminal.exitCode !== null &&
					!commandUnconfirmed
						? ` · exit ${terminal.exitCode}`
						: ''}</span
				>{/if}
		</div>
		{#if client && host}
			<div class="host-context">
				<strong>{host.name}</strong><code>{host.id} · {hostEndpoint}</code>
			</div>
		{/if}
		{#if workspace && client}
			<p class="path-label">Working directory <code>{workspace.path}</code></p>
			{#if activeGrant?.capabilities.execute}
				<p class="execution-warning">
					Commands run as the Runner’s OS account and can access beyond this project directory. The
					selected directory is a working location, not an OS sandbox.
				</p>
				<label class="risk-checkbox"
					><input
						type="checkbox"
						bind:checked={executionRiskAccepted}
						disabled={workspaceBusy || terminal !== null}
					/><span>I understand execution has this host-level access.</span></label
				>
				<p class="muted hint">
					This project session runs an executable with separate arguments and streams bounded
					output. It has no interactive terminal, shell parser, or PTY.
				</p>
				{#if !terminal}
					<button
						on:click={createCommandSession}
						disabled={!executionRiskAccepted || workspaceBusy || terminalBusy}
						>{terminalBusy ? 'Creating…' : 'Create command session'}</button
					>
				{:else}
					<form on:submit|preventDefault={runCommand}>
						<div class="command-fields">
							<div>
								<label for="companion-executable">Executable</label><input
									id="companion-executable"
									bind:value={executable}
									placeholder="python"
									spellcheck="false"
									autocapitalize="none"
									disabled={terminal.status === 'running' ||
										terminal.status === 'closed' ||
										commandUnconfirmed ||
										workspaceBusy ||
										terminalBusy ||
										stoppingTerminal ||
										stopUnconfirmed}
								/>
							</div>
							<div>
								<label for="companion-arguments">Arguments (JSON array of strings)</label><input
									id="companion-arguments"
									bind:value={argumentsJson}
									placeholder={'["--version"]'}
									spellcheck="false"
									autocapitalize="none"
									disabled={terminal.status === 'running' ||
										terminal.status === 'closed' ||
										commandUnconfirmed ||
										workspaceBusy ||
										terminalBusy ||
										stoppingTerminal ||
										stopUnconfirmed}
								/>
							</div>
						</div>
						<div class="field-row command-actions">
							<button
								type="submit"
								class="primary"
								disabled={!executable.trim() ||
									terminal.status === 'running' ||
									terminal.status === 'closed' ||
									commandUnconfirmed ||
									workspaceBusy ||
									terminalBusy ||
									stoppingTerminal ||
									stopUnconfirmed}>{terminalBusy ? 'Starting…' : 'Run command'}</button
							>
							{#if commandUnconfirmed}
								<button
									type="button"
									on:click={checkCommandStatus}
									disabled={workspaceBusy || terminalBusy || stoppingTerminal || stopUnconfirmed}
									>Check command status</button
								>
							{/if}
							<button type="button" on:click={stopCommandSession} disabled={stoppingTerminal}
								>{stoppingTerminal ? 'Stopping…' : 'Stop and remove session'}</button
							>
						</div>
					</form>
					<pre class="terminal-output" aria-label="Command output"><code
							>{terminalOutput || 'Command output will appear here.'}</code
						></pre>
					<p class="muted hint">
						Output retains the latest 256 KiB. Disconnecting or leaving this page requests session
						revocation; host expiry also stops commands.
					</p>
				{/if}
			{:else}
				<p class="empty-state">
					This folder grant has no execute permission. Enable execution explicitly on the host
					before creating a command session.
				</p>
			{/if}
		{:else}
			<p class="empty-state">
				Select a project on a paired execution host before creating a command session.
			</p>
		{/if}
	</section>
</div>

<style>
	.companion-page {
		width: 100%;
		height: 100dvh;
		overflow-y: auto;
		padding: 24px clamp(16px, 3vw, 40px) 100px;
		color: inherit;
	}
	.page-heading {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		margin-bottom: 24px;
	}
	.sidebar-toggle {
		flex-shrink: 0;
		padding: 8px;
	}
	.eyebrow {
		margin: 0 0 4px;
		color: var(--buddy-soft-text, #667368);
		font-size: 12px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	h1 {
		font-size: clamp(22px, 3vw, 30px);
		font-weight: 650;
		line-height: 1.25;
		margin: 0 0 8px;
	}
	h2 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 16px;
	}
	.muted {
		color: var(--buddy-soft-text, #667368);
		font-size: 14px;
		line-height: 1.6;
	}
	.hint {
		margin: 12px 0 0;
		font-size: 12px;
	}
	.panel {
		min-width: 0;
		padding: 22px;
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 18px;
		background: var(--buddy-panel, #fff);
		margin-bottom: 20px;
	}
	.section-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 16px;
	}
	.section-heading h2 {
		margin: 0;
	}
	.status {
		flex-shrink: 0;
		font-size: 12px;
		border-radius: 20px;
		padding: 4px 10px;
		background: var(--buddy-stage, #faf8f5);
	}
	.status.connected {
		color: #27754f;
		background: #e5f4e8;
	}
	label {
		display: block;
		font-size: 13px;
		font-weight: 500;
		margin: 0 0 6px;
	}
	input,
	select,
	textarea {
		width: 100%;
		min-width: 0;
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 9px;
		background: var(--buddy-stage, #faf8f5);
		color: inherit;
		padding: 10px 12px;
		font-size: 14px;
	}
	input:focus-visible,
	select:focus-visible,
	textarea:focus-visible,
	button:focus-visible {
		outline: 2px solid #438d65;
		outline-offset: 3px;
	}
	button {
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 9px;
		padding: 10px 14px;
		font-size: 13px;
		font-weight: 550;
		line-height: 1.35;
		cursor: pointer;
		background: var(--buddy-stage, #faf8f5);
		color: inherit;
	}
	button:hover:not(:disabled) {
		border-color: #438d65;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	button.primary {
		background: #27634b;
		border-color: #27634b;
		color: white;
	}
	.field-row {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.field-row input {
		flex: 1;
	}
	.field-row button {
		flex-shrink: 0;
	}
	.pair-form {
		margin-top: 20px;
	}
	.host-identity {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 18px;
		border-left: 3px solid #438d65;
		padding: 10px 14px;
		margin-top: 16px;
	}
	.host-identity span {
		font-size: 12px;
		color: var(--buddy-soft-text, #667368);
	}
	.host-identity code {
		flex-basis: 100%;
		font-size: 12px;
		overflow-wrap: anywhere;
	}
	.session-row {
		justify-content: space-between;
		margin-top: 16px;
	}
	.workspace-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr);
		gap: 20px;
	}
	.host-context {
		display: flex;
		flex-direction: column;
		gap: 3px;
		padding-bottom: 12px;
		margin-bottom: 14px;
		border-bottom: 1px solid var(--buddy-edge, #dde6df);
	}
	.host-context strong {
		font-size: 13px;
	}
	.host-context code {
		font-size: 11px;
		color: var(--buddy-soft-text, #667368);
		overflow-wrap: anywhere;
	}
	.path-label {
		margin: 12px 0;
		font-size: 12px;
		overflow-wrap: anywhere;
	}
	.path-label code {
		display: block;
		margin-top: 4px;
		font-size: 13px;
	}
	.capabilities {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.capabilities span {
		font-size: 11px;
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 5px;
		padding: 3px 6px;
	}
	.directory-toolbar {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 16px;
	}
	.directory-toolbar code {
		min-width: 0;
		flex: 1;
		font-size: 12px;
		overflow-wrap: anywhere;
	}
	.directory-toolbar button {
		padding: 6px 9px;
	}
	.directory-list {
		min-height: 140px;
		max-height: 340px;
		overflow-y: auto;
		margin: 12px 0;
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 9px;
	}
	.entry {
		display: flex;
		width: 100%;
		gap: 12px;
		align-items: center;
		border: 0;
		border-bottom: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 0;
		background: transparent;
		padding: 10px 12px;
		text-align: left;
	}
	.entry span:last-child {
		overflow-wrap: anywhere;
		min-width: 0;
	}
	.entry:last-child {
		border-bottom: 0;
	}
	.entry-kind {
		width: 40px;
		font-size: 10px;
		flex-shrink: 0;
		color: var(--buddy-soft-text, #667368);
	}
	.select-project {
		width: 100%;
	}
	.empty-state {
		padding: 18px 10px;
		color: var(--buddy-soft-text, #667368);
		font-size: 14px;
		line-height: 1.6;
	}
	.selected-project {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 18px;
		font-size: 12px;
		overflow-wrap: anywhere;
	}
	.selected-project strong {
		font-size: 14px;
	}
	.file-editor {
		display: block;
		height: 310px;
		resize: vertical;
		margin: 8px 0 14px;
		font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
		font-size: 13px;
		line-height: 1.5;
		tab-size: 4;
	}
	.message {
		border: 1px solid var(--buddy-edge, #dde6df);
		border-radius: 10px;
		padding: 12px 16px;
		margin-bottom: 16px;
		font-size: 14px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}
	.error {
		border-color: #d69b87;
		background: #fbede7;
		color: #832e18;
	}
	.notice {
		background: var(--buddy-panel, #fff);
	}
	.execution-warning {
		padding: 12px 14px;
		border: 1px solid #d3b578;
		border-radius: 9px;
		font-size: 14px;
		line-height: 1.5;
		margin: 14px 0;
	}
	.risk-checkbox {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		font-size: 13px;
		line-height: 1.5;
	}
	.risk-checkbox input {
		width: 16px;
		height: 16px;
		flex-shrink: 0;
		margin-top: 2px;
		accent-color: #27634b;
	}
	.command-fields {
		display: grid;
		grid-template-columns: 1fr 2fr;
		gap: 12px;
		margin: 18px 0 12px;
	}
	.command-actions {
		justify-content: flex-start;
		margin-bottom: 14px;
	}
	.terminal-output {
		max-height: 340px;
		min-height: 120px;
		overflow: auto;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		background: #131a16;
		color: #d9efdf;
		border-radius: 10px;
		padding: 16px;
		font-size: 13px;
		line-height: 1.5;
	}
	@media (max-width: 800px) {
		.workspace-grid {
			grid-template-columns: minmax(0, 1fr);
			gap: 0;
		}
		.companion-page {
			padding: 16px 12px 100px;
		}
		.panel {
			padding: 16px;
		}
		.command-fields {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 480px) {
		.field-row {
			flex-wrap: wrap;
		}
		.field-row input {
			flex-basis: 100%;
		}
		.field-row button {
			flex: 1;
		}
		.session-row p {
			flex-basis: 100%;
		}
		.section-heading {
			align-items: flex-start;
		}
		.page-heading {
			gap: 8px;
		}
		.sidebar-toggle {
			padding: 6px;
		}
	}
</style>
