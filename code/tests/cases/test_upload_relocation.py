"""Uploads stay readable and writable after moving an instance's upload store.

:module: resource.service
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from management_portal.hatg.contracts import InstructionError
from management_portal.resources.isa import complete_upload, read_upload


class UploadRelocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root / "retired" / "uploads"
        self.new = self.root / "instance" / "uploads"
        self.upload_id = "upload_0123456789abcdef"
        self.directory = self.old / self.upload_id
        self.directory.mkdir(parents=True)
        self.environment = patch.dict(os.environ, PORTAL_UPLOAD_ROOT=str(self.new))
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def relocate(self, *, ready=True, filename="picture.png"):
        meta = {
            "upload_id": self.upload_id, "created_by": "user:alice",
            "purpose": "attachment", "file_name": filename,
            "path": str(self.directory / filename), "ready": ready,
            "size": 4, "media_type": "application/octet-stream",
        }
        (self.directory / "meta.json").write_text(json.dumps(meta))
        if ready:
            (self.directory / filename).write_bytes(b"data")
        self.new.parent.mkdir(parents=True)
        shutil.move(str(self.old), str(self.new))
        return self.new / self.upload_id

    def test_read_uses_relocated_file_when_old_directory_is_gone(self):
        current = self.relocate()
        meta = read_upload(self.upload_id)
        self.assertFalse(self.old.exists())
        self.assertEqual(Path(meta["path"]), current / "picture.png")
        self.assertEqual(Path(meta["path"]).read_bytes(), b"data")

    def test_existing_old_path_cannot_redirect_read_to_another_instance(self):
        current = self.relocate()
        self.directory.mkdir(parents=True)
        (self.directory / "picture.png").write_bytes(b"wrong instance")
        self.assertEqual(Path(read_upload(self.upload_id)["path"]), current / "picture.png")

    def test_pending_upload_finishes_in_new_instance_without_persisting_path(self):
        current = self.relocate(ready=False)
        complete_upload(self.upload_id, b"data", actor="user:alice")
        self.assertFalse(self.old.exists())
        self.assertEqual((current / "picture.png").read_bytes(), b"data")
        self.assertNotIn("path", json.loads((current / "meta.json").read_text()))
        self.assertTrue(read_upload(self.upload_id)["ready"])

    def test_metadata_cannot_escape_upload_directory(self):
        current = self.relocate()
        path = current / "meta.json"
        meta = json.loads(path.read_text())
        for name in ("../outside", "/outside", "..", r"..\outside"):
            with self.subTest(name=name):
                path.write_text(json.dumps({**meta, "file_name": name}))
                with self.assertRaises(InstructionError):
                    read_upload(self.upload_id)
        path.write_text(json.dumps(meta))
        (current / "picture.png").unlink()
        (current / "picture.png").symlink_to(self.root / "outside")
        with self.assertRaises(InstructionError):
            read_upload(self.upload_id)


if __name__ == "__main__":
    unittest.main()
