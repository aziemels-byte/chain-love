"""Regression tests for atomic NDJSON archive publication (issue #3832).

Run from the json-tools repository root:
    python -m unittest tools.test_create_ndjson_atomic -v
"""
from __future__ import annotations

import errno
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import create_ndjson


class AtomicArchiveTests(unittest.TestCase):
    def test_failed_member_write_keeps_previous_archive(self):
        old_data = {"apis": [{"slug": "old"}]}
        new_data = {"apis": [{"slug": "new"}], "wallets": [{"slug": "new-wallet"}]}

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "somnia-ndjson.tar.gz"
            with patch.object(create_ndjson, "JSON_DIR", directory):
                create_ndjson.write_tar("somnia", old_data)
                previous = target.read_bytes()
                original_write = create_ndjson.write_ndjson_file

                def fail_on_second_member(archive, name, items):
                    if name == "wallets.ndjson":
                        raise OSError(errno.ENOSPC, "simulated disk full")
                    return original_write(archive, name, items)

                with patch.object(create_ndjson, "write_ndjson_file",
                                  side_effect=fail_on_second_member):
                    with self.assertRaises(OSError):
                        create_ndjson.write_tar("somnia", new_data)

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_failed_atomic_replace_keeps_previous_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "demo-ndjson.tar.gz"
            with patch.object(create_ndjson, "JSON_DIR", directory):
                create_ndjson.write_tar("demo", {"apis": [{"slug": "old"}]})
                previous = target.read_bytes()

                with patch.object(create_ndjson.os, "replace",
                                  side_effect=OSError(errno.EACCES, "replace denied")):
                    with self.assertRaises(OSError):
                        create_ndjson.write_tar("demo", {"apis": [{"slug": "new"}]})

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_successful_rebuild_replaces_archive_and_preserves_members(self):
        new_data = {
            "apis": [{"slug": "new", "price": "€200/month"}],
            "columns": {"apis": ["slug", "price"]},
            "meta": {"categories": [{"key": "apis", "label": "APIs"}]},
        }

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "filecoin-ndjson.tar.gz"
            with patch.object(create_ndjson, "JSON_DIR", directory):
                create_ndjson.write_tar("filecoin", {"apis": [{"slug": "old"}]})
                previous = target.read_bytes()
                create_ndjson.write_tar("filecoin", new_data)

            self.assertNotEqual(target.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [target])
            with tarfile.open(target, "r:gz") as archive:
                self.assertEqual(archive.getnames(),
                                 ["apis.ndjson", "columns/apis.json",
                                  "meta/categories.ndjson"])
                api_file = archive.extractfile("apis.ndjson")
                columns_file = archive.extractfile("columns/apis.json")
                meta_file = archive.extractfile("meta/categories.ndjson")
                self.assertIsNotNone(api_file)
                self.assertIsNotNone(columns_file)
                self.assertIsNotNone(meta_file)
                self.assertEqual(json.loads(api_file.read().decode("utf-8").strip()),
                                 new_data["apis"][0])
                self.assertEqual(json.loads(columns_file.read().decode("utf-8")),
                                 new_data["columns"]["apis"])
                self.assertEqual(json.loads(meta_file.read().decode("utf-8").strip()),
                                 new_data["meta"]["categories"][0])


if __name__ == "__main__":
    unittest.main()
