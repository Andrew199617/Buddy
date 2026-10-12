"""Encryption of stored sign-ins, and the table that holds them.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_subscription_credentials.py -v

The migration runs against a temporary SQLite file; nothing touches Buddy's database.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

import sqlalchemy as sa  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402

from open_webui.utils.subscriptions.vault import Vault, VaultError, fingerprint  # noqa: E402

MIGRATION = ROOT / 'backend' / 'open_webui' / 'migrations' / 'versions' / 'b6d1f0c3a9e4_add_subscription_credential_table.py'
FIXTURE_TOKEN = 'sk-ant-oat01-' + 'F' * 40 + 'Tail'


def load_migration():
    spec = importlib.util.spec_from_file_location('subscription_credential_migration', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VaultTests(unittest.TestCase):
    def test_round_trip_never_stores_plaintext(self):
        vault = Vault('fixture-secret')
        encrypted = vault.encrypt(FIXTURE_TOKEN)
        self.assertNotIn(FIXTURE_TOKEN, encrypted)
        self.assertEqual(vault.decrypt(encrypted), FIXTURE_TOKEN)

    def test_a_different_key_cannot_decrypt(self):
        encrypted = Vault('fixture-secret').encrypt(FIXTURE_TOKEN)
        with self.assertRaises(VaultError):
            Vault('another-secret').decrypt(encrypted)

    def test_rotation_keeps_old_values_readable_and_writes_with_the_new_key(self):
        encrypted_with_old = Vault('old-secret').encrypt(FIXTURE_TOKEN)
        rotated = Vault('new-secret', ['old-secret'])
        self.assertEqual(rotated.decrypt(encrypted_with_old), FIXTURE_TOKEN)
        encrypted_with_new = rotated.encrypt(FIXTURE_TOKEN)
        self.assertEqual(Vault('new-secret').decrypt(encrypted_with_new), FIXTURE_TOKEN)

    def test_garbage_is_a_vault_error(self):
        with self.assertRaises(VaultError):
            Vault('fixture-secret').decrypt('not a token')

    def test_an_empty_secret_is_refused(self):
        with self.assertRaises(ValueError):
            Vault('')

    def test_representation_reveals_nothing(self):
        self.assertEqual(repr(Vault('fixture-secret')), '<Vault>')

    def test_fingerprint_shows_only_a_prefix_and_the_last_characters(self):
        self.assertEqual(fingerprint(FIXTURE_TOKEN), 'sk-ant-…Tail')
        self.assertEqual(fingerprint('abcd1234wxyz'), '…wxyz')
        self.assertEqual(fingerprint('short'), '…')


class CredentialMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-credential-migration-')
        self.addCleanup(directory.cleanup)
        self.engine = sa.create_engine(f'sqlite:///{Path(directory.name) / "fixture.db"}')
        self.addCleanup(self.engine.dispose)
        self.migration = load_migration()

    def run_step(self, step):
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def test_upgrade_is_idempotent_and_enforces_one_secret_per_kind(self):
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.upgrade)
        inspector = sa.inspect(self.engine)
        self.assertIn('subscription_credential', inspector.get_table_names())
        indexes = {index['name']: index for index in inspector.get_indexes('subscription_credential')}
        self.assertTrue(indexes['idx_subscription_credential_owner']['unique'])
        row = {
            'user_id': 'user-1', 'provider': 'claude', 'kind': 'plan_token', 'secret': 'encrypted',
            'created_at': 1, 'updated_at': 1,
        }
        with self.engine.begin() as connection:
            connection.execute(sa.text(
                'INSERT INTO subscription_credential (id, user_id, provider, kind, secret, created_at, updated_at) '
                'VALUES (:id, :user_id, :provider, :kind, :secret, :created_at, :updated_at)'
            ), {'id': 'first', **row})
        with self.assertRaises(sa.exc.IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(sa.text(
                    'INSERT INTO subscription_credential (id, user_id, provider, kind, secret, created_at, updated_at) '
                    'VALUES (:id, :user_id, :provider, :kind, :secret, :created_at, :updated_at)'
                ), {'id': 'second', **row})

    def test_downgrade_removes_the_table(self):
        self.run_step(self.migration.upgrade)
        self.run_step(self.migration.downgrade)
        self.assertNotIn('subscription_credential', sa.inspect(self.engine).get_table_names())


if __name__ == '__main__':
    unittest.main()
