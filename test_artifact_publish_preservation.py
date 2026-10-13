from pathlib import Path
import tempfile
import unittest

from artifact_publish import publish_artifacts


class ArtifactPublishPreservationTest(unittest.TestCase):
    def test_first_publication_then_reuse_preserves_every_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fictional.jsonl"
            original = b'{"message":"fictional original"}\n'
            source.write_bytes(original)
            root = Path(directory) / "output"
            artifacts = [
                (root / "original" / source.name, original),
                (root / "compacted" / source.name, b'{"message":"candidate"}\n'),
                (root / "reports" / "report.json", b'{"report":true}\n'),
                (root / "manifests" / "manifest.json", b'{"complete":true}\n'),
            ]
            manifest = artifacts[-1][0]
            publish_artifacts(source, original, root, artifacts, manifest)
            self.assertEqual([(path, path.read_bytes()) for path, _ in artifacts], artifacts)
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                      for path, _ in artifacts}
            entries = set(root.rglob("*"))
            for current in (original, b'{"message":"fictional changed source"}\n'):
                source.write_bytes(current)
                replacements = [(path, current) for path, _ in artifacts]
                with self.assertRaisesRegex(FileExistsError, "fresh output root"):
                    publish_artifacts(source, current, root, replacements, manifest)
                self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                                  for path in before}, before)
                self.assertEqual(set(root.rglob("*")), entries)
                self.assertEqual(source.read_bytes(), current)

    def test_any_existing_destination_refuses_partial_publication(self):
        for name in ("original.jsonl", "candidate.jsonl", "report.json", "manifest.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "fictional.jsonl"
                source.write_bytes(b'{}\n')
                paths = [root / item for item in
                         ("original.jsonl", "candidate.jsonl", "report.json", "manifest.json")]
                existing = root / name
                existing.write_bytes(b"preserved")
                before = (existing.stat().st_mtime_ns, existing.stat().st_ctime_ns)
                with self.assertRaises(FileExistsError):
                    publish_artifacts(source, source.read_bytes(), root,
                                      [(path, b"replacement") for path in paths], paths[-1])
                self.assertEqual(existing.read_bytes(), b"preserved")
                self.assertEqual((existing.stat().st_mtime_ns, existing.stat().st_ctime_ns), before)
                self.assertEqual([path for path in paths if path.exists()], [existing])
                self.assertFalse(list(root.glob(".building-*")))


if __name__ == "__main__":
    unittest.main()
