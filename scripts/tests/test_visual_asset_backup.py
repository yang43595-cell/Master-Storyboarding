"""Backup failures must remain visible and retryable without false completion."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_backup as backup
import visual_asset_management as management


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        source = root / 'original.png'
        source.write_bytes(b'original image bytes')
        self.asset = dict(id='one', name='茅台', category='物品', role='候选', project='test',
                          original=str(source), source=str(source), status='confirmed',
                          width=1600, height=900, backup={'status': 'pending'})
        self.lib = Mock(lock=threading.RLock(), vault=root, root=root)
        self.lib.items.side_effect = lambda: [self.asset]
        self.lib.get.side_effect = lambda ident: self.asset
        self.lib.product_categories.return_value = []
        self.mgr = Mock(inside=management.inside, write_index=management.write_index)

    def test_blocked_then_retry_saves_before_marking_saved_and_is_idempotent(self):
        self.mgr.guard.side_effect = ValueError('资产库正在由另一项操作更新')
        with self.assertRaisesRegex(ValueError, '资产库正在由另一项操作更新'):
            backup.retry(self.lib, self.mgr)
        self.assertEqual(self.asset['backup']['status'], 'pending')
        self.assertFalse((self.lib.vault / '资产库').exists())
        self.mgr.guard.reset_mock(side_effect=True)
        result = backup.retry(self.lib, self.mgr)
        self.assertEqual(result['count'], 1)
        target = Path(self.asset['backup']['path'])
        self.assertEqual(target.read_bytes(), Path(self.asset['original']).read_bytes())
        index = json.loads((self.lib.vault / '资产库/资产索引.json').read_text(encoding='utf-8'))
        self.assertEqual(len(index['assets']), 1)
        self.assertEqual(backup.retry(self.lib, self.mgr)['count'], 0)

    def test_unlock_failure_never_marks_backup_saved(self):
        def gate(vault, action):
            if action == 'finish':
                raise ValueError('释放资产库写入锁失败')
        self.mgr.guard.side_effect = gate
        with self.assertRaisesRegex(ValueError, '释放资产库写入锁失败'):
            backup.retry(self.lib, self.mgr)
        self.assertEqual(self.asset['backup']['status'], 'pending')
        self.mgr.guard.assert_called_with(self.lib.vault, 'abort')

    def test_no_vault_is_not_success(self):
        self.lib.vault = None
        with self.assertRaisesRegex(ValueError, '未配置知识库'):
            backup.retry(self.lib, self.mgr)
        self.assertEqual(self.asset['backup']['status'], 'pending')

    def test_body_character_saves_to_its_own_vault_category(self):
        self.asset.update(name='猫咪外观', category='角色外观')
        original = Path(self.asset['original']).read_bytes()
        backup.retry(self.lib, management)
        target = Path(self.asset['original'])
        self.assertIn('角色外观', target.parts)
        self.assertTrue(target.name.startswith('BODY-'))
        self.assertEqual(target.read_bytes(), original)
        index = json.loads((self.lib.vault / '资产库/资产索引.json').read_text(encoding='utf-8'))
        self.assertEqual(index['assets'][0]['category'], '角色外观')

    def test_local_backup_succeeds_without_git_or_vault_guard(self):
        (self.lib.vault / 'unrelated-draft.json').write_text('{}', encoding='utf-8')
        result = backup.retry(self.lib, management)
        self.assertEqual(result['count'], 1)
        self.assertEqual(self.asset['backup']['status'], 'saved')
        self.assertTrue(Path(self.asset['backup']['path']).is_file())
        self.assertFalse((self.lib.vault / '.git').exists())
