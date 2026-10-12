// Where the folder browser reads folders from: a computer's whole disk through
// Buddy, or only the folders shared in the Buddy app there. Both give the
// browser the same view, so it does not care which one it shows.

import type { CompanionGrant } from '$lib/apis/companion';
import { RelayCompanionClient } from '$lib/apis/companion-relay';
import { listMachineFolders, type FolderListing, type FolderRoot } from '$lib/apis/subscriptions';
import { grantContaining, joinSharedPath, parentWithinRoot } from './project-folders';

export type BrowsePlace = { name: string; path: string; kind: FolderRoot['kind'] | 'shared' };

export type BrowseEntry = {
	name: string;
	path: string;
	hidden: boolean;
	link: boolean;
	selectable: boolean;
};

export type Crumb = { name: string; path: string };

export type BrowseView = {
	// '' while showing the places to start from.
	path: string;
	// Where Up goes; null goes back to the places.
	parent: string | null;
	crumbs: Crumb[];
	places: BrowsePlace[];
	entries: BrowseEntry[];
	truncated: boolean;
	// Whether the folder shown can be used as a project folder.
	selectable: boolean;
};

export type FolderSource = {
	start(): Promise<BrowseView>;
	open(path: string, options: { hidden: boolean; prefix: string }): Promise<BrowseView>;
	// Whether hidden folders can be shown at all.
	showsHidden: boolean;
	close(): void;
};

function startView(places: BrowsePlace[]): BrowseView {
	return {
		path: '',
		parent: null,
		crumbs: [],
		places,
		entries: [],
		truncated: false,
		selectable: false
	};
}

function separatorFor(platform: string | null): string {
	if (platform === 'win32') return '\\';
	return '/';
}

/** Breadcrumbs from a drive or / down to `path`, each with its own full path. */
export function pathCrumbs(path: string, platform: string | null): Crumb[] {
	const separator = separatorFor(platform);
	const parts = path.split(/[\\/]+/).filter(Boolean);
	const crumbs: Crumb[] = [];
	let current = '';
	if (platform !== 'win32') {
		crumbs.push({ name: '/', path: '/' });
		current = '';
	}
	for (const part of parts) {
		if (platform === 'win32' && current === '') {
			// The drive: "C:" opens as "C:\".
			current = `${part}${separator}`;
		} else if (current.endsWith(separator)) {
			current = `${current}${part}`;
		} else {
			current = `${current}${separator}${part}`;
		}
		crumbs.push({ name: part, path: current });
	}
	return crumbs;
}

function viewFromListing(listing: FolderListing, platform: string | null): BrowseView {
	return {
		path: listing.path,
		parent: listing.parent,
		crumbs: pathCrumbs(listing.path, platform),
		places: [],
		entries: listing.entries,
		truncated: listing.truncated,
		// A whole drive is not a project folder.
		selectable: listing.parent !== null
	};
}

/** Any folder on one of the person's computers, through Buddy. Folder names only. */
export function computerFolderSource(machineId: string, platform: string | null): FolderSource {
	return {
		showsHidden: true,
		async start() {
			const listing = await listMachineFolders(localStorage.token, machineId);
			return startView(listing.roots);
		},
		async open(path, options) {
			const listing = await listMachineFolders(localStorage.token, machineId, {
				path,
				hidden: options.hidden,
				prefix: options.prefix
			});
			return viewFromListing(listing, platform);
		},
		close() {}
	};
}

/** Only the folders shared in the Buddy app there, through the Project tab's relay. */
export function sharedFolderSource(machineId: string, platform: string | null): FolderSource {
	const client = new RelayCompanionClient(machineId, localStorage.token);
	let grants: CompanionGrant[] = [];
	const separator = separatorFor(platform);

	return {
		showsHidden: false,
		async start() {
			const all = await client.getGrants();
			grants = all.filter((grant) => grant.available !== false && grant.capabilities.read);
			return startView(
				grants.map((grant) => ({ name: grant.name, path: grant.path, kind: 'shared' }))
			);
		},
		async open(path, options) {
			const match = grantContaining(grants, path, platform);
			if (!match) throw new Error('That folder is not shared in the Buddy app.');
			const grant = match.grant;
			const listing = await client.getDirectory(grant.id, match.relative);
			const wanted = options.prefix.toLocaleLowerCase();
			const entries: BrowseEntry[] = [];
			for (const entry of listing.entries) {
				if (entry.type !== 'directory') continue;
				if (wanted && !entry.name.toLocaleLowerCase().startsWith(wanted)) continue;
				const entryPath = joinSharedPath(grant.path, entry.path, platform);
				entries.push({
					name: entry.name,
					path: entryPath,
					hidden: false,
					link: false,
					selectable: true
				});
			}
			let parent: string | null = null;
			if (listing.path)
				parent = joinSharedPath(grant.path, parentWithinRoot(listing.path), platform);
			const crumbs: Crumb[] = [{ name: grant.name, path: grant.path }];
			let current = grant.path.replace(/[\\/]+$/, '');
			for (const part of listing.path.split('/').filter(Boolean)) {
				current = `${current}${separator}${part}`;
				crumbs.push({ name: part, path: current });
			}
			return {
				path,
				parent,
				crumbs,
				places: [],
				entries,
				truncated: false,
				selectable: true
			};
		},
		close() {
			void client.disconnect().catch(() => {});
		}
	};
}
