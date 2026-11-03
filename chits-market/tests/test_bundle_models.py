"""Model-asset packaging checks with synthetic GGUF-shaped byte fixtures.

These tests validate packaging, hashes, sharding and safe filesystem changes.
The fixtures are not language models; no inference claim follows from them.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / 'tools/bundle-models.py'
SPEC = importlib.util.spec_from_file_location('chits_bundle_models', SCRIPT)
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)
REVISION = '0123456789abcdef0123456789abcdef01234567'


class BundleModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.output = self.directory / 'assets' / 'models'
        self.text = self.directory / 'text weights.gguf'
        self.vision = self.directory / 'vision.gguf'
        self.projector = self.directory / 'projector.gguf'
        self.text.write_bytes(b'GGUF' + bytes(range(1, 93)))
        self.vision.write_bytes(b'GGUF' + bytes(range(101, 199)))
        self.projector.write_bytes(b'GGUF' + bytes(range(200, 255)))

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, **options):
        return BUNDLE.bundle_models(output=self.output, shard_size=16, **options)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.output.iterdir() if p.is_file()}

    def assert_reconstructs(self, manifest, expected):
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual(manifest['generator'], BUNDLE.GENERATOR)
        self.assertEqual({e['role'] for e in manifest['files']}, set(expected))
        names = {'manifest.json'}
        for entry in manifest['files']:
            data = bytearray()
            for index, part in enumerate(entry['parts']):
                self.assertEqual(part['path'], f"models/{entry['role']}-{index:03d}.bin")
                name = Path(part['path']).name
                names.add(name)
                body = (self.output / name).read_bytes()
                self.assertEqual(len(body), part['size'])
                self.assertEqual(hashlib.sha256(body).hexdigest(), part['sha256'])
                self.assertLessEqual(len(body), 16)
                data.extend(body)
            self.assertEqual(data, expected[entry['role']].read_bytes())
            self.assertEqual(len(data), entry['size'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            self.assertEqual(entry['target'], BUNDLE.TARGETS[entry['role']])
        self.assertEqual({p.name for p in self.output.iterdir()}, names)
        self.assertEqual(json.loads((self.output / 'manifest.json').read_text()), manifest)

    def test_text_shards_reconstruct_exact_model_and_hashes(self):
        manifest = self.build(text=self.text)
        self.assert_reconstructs(manifest, {'text': self.text})
        self.assertGreater(len(manifest['files'][0]['parts']), 1)
        self.assertNotIn(str(self.text), json.dumps(manifest))
        self.assertNotIn('visionRevision', manifest)

    def test_full_bundle_pins_vision_pair_and_all_targets(self):
        manifest = self.build(text=self.text, vision=self.vision, projector=self.projector,
                              vision_revision=REVISION)
        self.assertEqual(manifest['visionRevision'], REVISION)
        self.assert_reconstructs(manifest, {'text': self.text, 'vision': self.vision,
                                           'projector': self.projector})

    def test_vision_only_bundle_is_supported(self):
        manifest = self.build(vision=self.vision, projector=self.projector, vision_revision=REVISION)
        self.assert_reconstructs(manifest, {'vision': self.vision, 'projector': self.projector})

    def test_no_inputs_or_half_vision_does_not_create_output(self):
        for arguments in ({}, {'vision': self.vision}, {'projector': self.projector},
                          {'vision': self.vision, 'projector': self.projector},
                          {'vision_revision': REVISION}):
            with self.subTest(arguments=arguments), self.assertRaises(BUNDLE.BundleError):
                self.build(**arguments)
            self.assertFalse(self.output.exists())

    def test_invalid_or_missing_gguf_never_creates_manifest(self):
        invalid = self.directory / 'invalid.gguf'
        invalid.write_bytes(b'not model weights')
        too_short = self.directory / 'short.gguf'
        too_short.write_bytes(b'GGUF')
        for path in (invalid, too_short, self.directory / 'missing.gguf'):
            with self.subTest(path=path), self.assertRaises(BUNDLE.BundleError):
                self.build(text=path)
            self.assertFalse(self.output.exists())

    def test_bad_vision_revision_is_rejected(self):
        for value in ('main', '../bad', 'a' * 39, 'g' * 40):
            with self.subTest(revision=value), self.assertRaises(BUNDLE.BundleError):
                self.build(vision=self.vision, projector=self.projector, vision_revision=value)
        self.assertFalse(self.output.exists())

    def test_refuses_arbitrary_output_names_and_symlinks(self):
        with self.assertRaises(BUNDLE.BundleError):
            BUNDLE.bundle_models(output=self.directory / 'important-files', text=self.text)
        self.output.parent.mkdir()
        outside = self.directory / 'outside'
        outside.mkdir()
        self.output.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=self.text, clear=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_refuses_output_parent_symlink_and_source_symlink(self):
        real = self.directory / 'real'
        real.mkdir()
        self.output.parent.symlink_to(real, target_is_directory=True)
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=self.text)
        self.output.parent.unlink()
        linked = self.directory / 'linked.gguf'
        linked.symlink_to(self.text)
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=linked)

    def test_source_inside_output_is_never_deleted(self):
        self.output.mkdir(parents=True)
        source = self.output / 'my-original.gguf'
        source.write_bytes(self.text.read_bytes())
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=source, clear=True)
        self.assertEqual(source.read_bytes(), self.text.read_bytes())

    def test_unknown_files_block_clear_without_removing_anything(self):
        self.build(text=self.text)
        important = self.output / 'my-notes.txt'
        important.write_text('user notes')
        before = self.snapshot()
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=self.text, clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_modified_generated_parts_block_clear(self):
        manifest = self.build(text=self.text)
        part = self.output / Path(manifest['files'][0]['parts'][0]['path']).name
        part.write_bytes(b'changed user data')
        before = self.snapshot()
        with self.assertRaises(BUNDLE.BundleError):
            self.build(clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_existing_bundle_needs_explicit_clear_and_repack_is_repeatable(self):
        first = self.build(text=self.text)
        before = self.snapshot()
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=self.text)
        self.assertEqual(self.snapshot(), before)
        second = self.build(text=self.text, clear=True)
        self.assertEqual(first, second)
        self.assertEqual(self.snapshot(), before)
        self.text.write_bytes(b'GGUF' + b'new local weights' * 8)
        updated = self.build(text=self.text, clear=True)
        self.assert_reconstructs(updated, {'text': self.text})
        self.assertNotEqual(first['files'][0]['sha256'], updated['files'][0]['sha256'])

    def test_missing_source_or_failed_staging_preserves_existing_bundle(self):
        self.build(text=self.text)
        before = self.snapshot()
        with self.assertRaises(BUNDLE.BundleError):
            self.build(text=self.directory / 'missing.gguf', clear=True)
        with mock.patch.object(BUNDLE, '_write_model', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                self.build(text=self.text, clear=True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.output.parent.glob('.models-stage-*')))

    def test_owned_clear_is_repeatable_and_never_creates_fake_manifest(self):
        self.build(text=self.text)
        self.assertIsNone(self.build(clear=True))
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertIsNone(self.build(clear=True))
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_path_traversal_or_forged_ownership_cannot_be_cleared(self):
        manifest = self.build(text=self.text)
        manifest['files'][0]['parts'][0]['path'] = '../do-not-delete'
        (self.output / 'manifest.json').write_text(json.dumps(manifest))
        before = self.snapshot()
        with self.assertRaises(BUNDLE.BundleError):
            self.build(clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_shard_bounds_are_checked_before_filesystem_changes(self):
        for size in (0, -1, 64 * 1024 * 1024 + 1, True):
            with self.subTest(size=size), self.assertRaises(BUNDLE.BundleError):
                BUNDLE.bundle_models(output=self.output, text=self.text, shard_size=size)
        self.assertFalse(self.output.exists())

    def test_cli_help_and_error_and_success_exit_status(self):
        help_result = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('--vision-revision', help_result.stdout)
        missing = subprocess.run([sys.executable, str(SCRIPT), '--output', str(self.output)], capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.output.exists())
        result = subprocess.run([sys.executable, str(SCRIPT), '--text', str(self.text),
                                 '--output', str(self.output), '--shard-size-bytes', '16'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Packaged 1 local GGUF', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
