"""Bounded directory-coverage regressions, independent of external project media."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.core.product_refs import validate_documents, validate_image_documents


class DirectoryReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'documents'
        self.docs.mkdir()

    def cli(self, *extra):
        return subprocess.run([sys.executable, '-m', 'scripts.core.product_refs',
            '--project', str(self.root), '--directory', str(self.docs), *extra],
            capture_output=True, text=True)

    def test_missing_empty_wrong_type_and_unsupported_are_unchecked(self):
        for case in ('empty', 'unsupported', 'missing', 'file'):
            with self.subTest(case=case):
                if case == 'unsupported':
                    (self.docs / 'image.png').write_bytes(b'not a document')
                elif case == 'missing':
                    shutil.rmtree(self.docs)
                elif case == 'file':
                    self.docs.write_text('not a directory')
                for validate in (validate_documents, validate_image_documents):
                    errors = validate(self.root, self.docs)
                    self.assertTrue(errors)
                    self.assertIn('UNCHECKED', errors[0])
                result = self.cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('UNCHECKED', result.stdout)

    def test_existing_json_without_product_is_valid(self):
        (self.docs / 'candidate.JSON').write_text(json.dumps({'prompt_text': '普通道具'}))
        self.assertEqual(validate_documents(self.root, self.docs), [])
        self.assertEqual(validate_image_documents(self.root, self.docs), [])
        self.assertEqual(self.cli('--include-images').returncode, 0)

    def test_real_product_missing_reference_still_blocks(self):
        (self.docs / 'candidate.json').write_text(json.dumps({'category': 'REAL_PRODUCT'}))
        self.assertIn('REAL_PRODUCT', validate_documents(self.root, self.docs)[0])
        self.assertNotEqual(self.cli('--include-images').returncode, 0)

    def test_txt_only_image_scope_does_not_require_product_document(self):
        doc = self.docs / 'prompt.txt'
        doc.write_text('普通画面')
        result = self.cli('--include-images')
        self.assertEqual(result.returncode, 0)
        self.assertIn('checking TXT image references only', result.stdout)
        self.assertIn('UNCHECKED', validate_documents(self.root, self.docs)[0])
        self.assertNotEqual(self.cli().returncode, 0)
        doc.write_text('05_分镜/missing.png')
        self.assertNotEqual(self.cli('--include-images').returncode, 0)

    def test_reset_restore_and_retries_are_read_only(self):
        path = self.docs / 'candidate.json'
        source = b'{"status":"candidate","production_blocked":true}'
        path.write_bytes(source)
        self.assertEqual(self.cli().returncode, 0)
        shutil.rmtree(self.docs)
        for _ in range(2):
            self.assertNotEqual(self.cli().returncode, 0)
            self.assertFalse(self.docs.exists())
        self.docs.mkdir()
        path.write_bytes(source)
        for _ in range(2):
            self.assertEqual(self.cli().returncode, 0)
            self.assertEqual(path.read_bytes(), source)
        self.assertEqual(list(self.docs.iterdir()), [path])

    def test_one_valid_require_file_does_not_hide_missing_directory(self):
        (self.root / 'source.json').write_text('{}')
        shutil.rmtree(self.docs)
        self.assertNotEqual(self.cli('--require-file', 'source.json').returncode, 0)

    def test_symlink_loop_is_unchecked(self):
        self.docs.rmdir()
        self.docs.symlink_to(self.docs)
        self.assertTrue(validate_documents(self.root, self.docs))
        self.assertNotEqual(self.cli().returncode, 0)


if __name__ == '__main__':
    unittest.main()
