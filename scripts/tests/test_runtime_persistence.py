"""The asset catalog must survive failed writes and a complete service restart."""
import json
from pathlib import Path

import pytest
from PIL import Image

from visual_asset_library import Library
import visual_asset_backup as backup
import visual_asset_management as management


def test_failed_catalog_update_preserves_saved_assets_then_retries(tmp_path, monkeypatch):
    library = Library(tmp_path / "library", vault=tmp_path / "vault")
    source = tmp_path / "original.png"
    Image.new("RGB", (200, 200), "red").save(source)
    asset, _ = library.import_file(source, category="物品", project="audit")
    catalog = library.vault / "资产库" / "资产索引.json"
    original_catalog = catalog.read_bytes()
    canonical_image = Path(asset["original"])
    original_image = canonical_image.read_bytes()
    library.update({"id": asset["id"], "name": "updated name"})

    real_write = Path.write_text

    def fail_during_write(path, value, *args, **kwargs):
        if path.parent == catalog.parent and path.name.startswith(".tmp-"):
            real_write(path, '{"assets":[', *args, **kwargs)
            raise OSError("simulated disk full")
        return real_write(path, value, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "write_text", fail_during_write)
        with pytest.raises(ValueError, match="simulated disk full"):
            backup.retry(library, management)

    assert catalog.read_bytes() == original_catalog
    assert canonical_image.read_bytes() == original_image
    assert library.get(asset["id"])["backup"]["status"] == "pending"
    assert not list(catalog.parent.glob(".tmp-*"))
    assert backup.retry(library, management)["status"] == "saved"
    assert json.loads(catalog.read_text(encoding="utf-8"))["assets"][0]["name"] == "updated name"
    restarted = Library(library.root, vault=library.vault)
    assert restarted.get(asset["id"])["name"] == "updated name"
    assert Path(restarted.get(asset["id"])["original"]).read_bytes() == original_image


def test_library_vault_uses_dedicated_asset_storage_setting(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    # Windows expanduser uses USERPROFILE; keep this fixture inside tmp_path.
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('MSB_VAULT_ROOT', '~/old-external-knowledge')
    monkeypatch.delenv('MSB_ASSET_VAULT_ROOT', raising=False)
    local = Library(tmp_path / 'local/library')
    assert local.vault == tmp_path / 'local/vault'
    monkeypatch.setenv('MSB_ASSET_VAULT_ROOT', '~/media')
    library = Library(tmp_path / 'env-library')
    assert library.vault == tmp_path / 'media'
    explicit = Library(tmp_path / 'isolated-library', vault=tmp_path / 'isolated-vault')
    assert explicit.vault == tmp_path / 'isolated-vault'


def test_library_cannot_write_into_bundled_knowledge(tmp_path, monkeypatch):
    from core.knowledge_runtime import bundled_root
    monkeypatch.delenv('MSB_ASSET_VAULT_ROOT', raising=False)
    with pytest.raises(ValueError, match='内置知识包'):
        Library(tmp_path / 'library', vault=bundled_root())
    library = Library(tmp_path / 'library')
    assert library.vault == tmp_path / 'vault'
    assert library.vault != bundled_root()
