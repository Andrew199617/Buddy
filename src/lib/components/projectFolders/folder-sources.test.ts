import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listMachineFolders, getGrants, getDirectory, disconnect } = vi.hoisted(() => ({
	listMachineFolders: vi.fn(),
	getGrants: vi.fn(),
	getDirectory: vi.fn(),
	disconnect: vi.fn(async () => {})
}));

vi.mock('$lib/apis/subscriptions', () => ({ listMachineFolders }));
vi.mock('$lib/apis/companion-relay', () => ({
	RelayCompanionClient: class {
		getGrants = getGrants;
		getDirectory = getDirectory;
		disconnect = disconnect;
	}
}));

import { computerFolderSource, pathCrumbs, sharedFolderSource } from './folder-sources';

beforeEach(() => {
	vi.stubGlobal('localStorage', { token: 'fixture-token' });
	listMachineFolders.mockReset();
	getGrants.mockReset();
	getDirectory.mockReset();
});

describe('breadcrumbs', () => {
	it('start at the drive on Windows and at / elsewhere', () => {
		expect(pathCrumbs('C:\\Users\\sam', 'win32')).toEqual([
			{ name: 'C:', path: 'C:\\' },
			{ name: 'Users', path: 'C:\\Users' },
			{ name: 'sam', path: 'C:\\Users\\sam' }
		]);
		expect(pathCrumbs('/home/sam', 'linux')).toEqual([
			{ name: '/', path: '/' },
			{ name: 'home', path: '/home' },
			{ name: 'sam', path: '/home/sam' }
		]);
	});
});

describe('a computer’s whole disk', () => {
	it('starts at its drives and places, then lists one folder at a time', async () => {
		listMachineFolders.mockResolvedValueOnce({
			path: '',
			parent: null,
			roots: [{ name: 'C:', path: 'C:\\', kind: 'drive' }],
			entries: [],
			truncated: false
		});
		const source = computerFolderSource('pc-1', 'win32');
		const start = await source.start();
		expect(start.places).toEqual([{ name: 'C:', path: 'C:\\', kind: 'drive' }]);
		expect(start.path).toBe('');

		listMachineFolders.mockResolvedValueOnce({
			path: 'C:\\',
			parent: null,
			roots: [],
			entries: [{ name: 'code', path: 'C:\\code', hidden: false, link: false, selectable: true }],
			truncated: false
		});
		const drive = await source.open('C:\\', { hidden: true, prefix: 'co' });
		expect(listMachineFolders).toHaveBeenLastCalledWith('fixture-token', 'pc-1', {
			path: 'C:\\',
			hidden: true,
			prefix: 'co'
		});
		// A whole drive can be browsed but not chosen.
		expect(drive.selectable).toBe(false);
		expect(drive.entries.map((entry) => entry.path)).toEqual(['C:\\code']);
	});
});

describe('the folders shared in the Buddy app', () => {
	it('lists only folders inside the shared folders and goes back to them at the top', async () => {
		getGrants.mockResolvedValueOnce([
			{ id: 'g1', name: 'code', path: 'C:\\code', capabilities: { read: true }, available: true },
			{ id: 'g2', name: 'gone', path: 'D:\\gone', capabilities: { read: true }, available: false }
		]);
		const source = sharedFolderSource('pc-1', 'win32');
		const start = await source.start();
		expect(start.places.map((place) => place.name)).toEqual(['code']);

		getDirectory.mockResolvedValueOnce({
			path: 'site',
			entries: [
				{ name: 'src', path: 'site/src', type: 'directory' },
				{ name: 'README.md', path: 'site/README.md', type: 'file' }
			]
		});
		const site = await source.open('C:\\code\\site', { hidden: false, prefix: '' });
		expect(getDirectory).toHaveBeenLastCalledWith('g1', 'site');
		expect(site.entries.map((entry) => entry.path)).toEqual(['C:\\code\\site\\src']);
		expect(site.parent).toBe('C:\\code');
		expect(site.crumbs.map((crumb) => crumb.name)).toEqual(['code', 'site']);

		getDirectory.mockResolvedValueOnce({ path: '', entries: [] });
		const top = await source.open('C:\\code', { hidden: false, prefix: '' });
		expect(top.parent).toBeNull();
		await expect(source.open('E:\\elsewhere', { hidden: false, prefix: '' })).rejects.toThrow(
			'not shared'
		);
		source.close();
		expect(disconnect).toHaveBeenCalled();
	});
});
