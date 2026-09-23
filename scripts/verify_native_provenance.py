#!/usr/bin/env python3
"""Tiny fail-closed provenance fixtures; no media, compiler or game execution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_provenance import (ADDRESS_TARGET, RUN_PATHS, audit_cpu_inputs,
                               link_input_files, match_engine_audit, minimum_macos,
                               sha, validate_link_inventory, verify_snapshot)

INTERPRETER = 'CMakeFiles/flycast_libretro.dir/core/hw/sh4/interpr/sh4_interpreter.cpp.o'
OPCODES = 'CMakeFiles/flycast_libretro.dir/core/hw/sh4/interpr/sh4_opcodes.cpp.o'


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vt-provenance-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.reference = self.root / 'build/reference-clock'
        self.generated = self.root / 'build/generated/sh4'
        self.original = self.root / 'build/reference-source/flycast'
        self.corrected = self.root / 'build/clock-source/flycast'
        self.cpu_path = self.root / 'build/native/cpu/sh4/manifest.json'
        self.gen_path = self.generated / 'manifest.json'
        self.address = 'core/hw/mem/addrspace.cpp'
        self.header = 'core/hw/sh4/sh4_interpreter.h'
        for name in ('scripts/compile_sh4.py', 'scripts/build_sh4.py',
                     'scripts/compile_sh4_blocks.py', 'scripts/validate_sh4_blocks.py',
                     'Sources/Translated/sh4/templates/mapped_fetch.h'):
            self.write(self.root / name, 'fixture ' + name)
        self.write(self.root / 'Configuration/sh4-blocks.json', '{}')
        for path in ('Configuration/sh4-source.json', 'Configuration/sh4-bios-copies.json', 'Configuration/media.json'):
            self.write(self.root / path, '{"original":true}')
        old = 'static void* memInfo_ptr[0x100];\nvoid original_read16() {}\n'
        for tree in (self.original, self.corrected):
            self.write(tree / self.address, old)
            self.write(tree / self.header, 'clock = ' + ('8' if tree == self.original else '1'))
        for directory, tree in ((self.root / 'build/reference', self.original), (self.reference, self.corrected)):
            inventory = {'.': {'files': {p: sha(tree / p) for p in (self.address, self.header)}}}
            self.json(directory / 'upstream-files.json', inventory)
            manifest = {'upstreamFileInventorySHA256': sha(directory / 'upstream-files.json'),
                        'sh4ClockHz': 200000000, 'interpreterCycleMultiplier': 1}
            if tree == self.corrected:
                manifest['sourcePatches'] = [{'path': self.header,
                    'originalSHA256': sha(self.original / self.header),
                    'derivedSHA256': sha(self.corrected / self.header)}]
            self.json(directory / 'build-manifest.json', manifest)
        for name in ('vt_sh4_executor.cpp', 'game-targets.s', 'bios-targets.s',
                     'run_dispatch.h', 'mapped_fetch.h', 'plan.json', 'overlay/cycles.h'):
            self.write(self.generated / name, 'generated fixture ' + name)
        self.write(self.generated / 'addrspace.cpp', old.replace('static void*', '__attribute__((visibility("hidden"))) void*'))
        run = {'plan': 'plan.json', 'executorSource': 'vt_sh4_executor.cpp',
               'runDispatchHeader': 'run_dispatch.h', 'mappedFetchHeader': 'mapped_fetch.h',
               'derivedAddressSpaceSource': 'addrspace.cpp',
               'generatorSources': {p: sha(self.root / p) for p in ('scripts/compile_sh4_blocks.py', 'scripts/validate_sh4_blocks.py')},
               'templateSources': {'Sources/Translated/sh4/templates/mapped_fetch.h': sha(self.root / 'Sources/Translated/sh4/templates/mapped_fetch.h')},
               'configurationSHA256': sha(self.root / 'Configuration/sh4-blocks.json'),
               'mappedFetch': {'originalObjectTarget': ADDRESS_TARGET, 'replacementObject': 'addrspace.o',
                   'originalSource': self.address, 'originalSourceSHA256': sha(self.corrected / self.address),
                   'sourceInverseTransformVerified': True}}
        generated_pins = {str(p.relative_to(self.generated)): sha(p) for p in self.generated.rglob('*') if p.is_file()}
        self.generation = {'generatorSHA256': sha(self.root / 'scripts/compile_sh4.py'),
            'generated': generated_pins, 'inputSources': {self.address: sha(self.original / self.address)},
            'headers': {self.header: sha(self.original / self.header)}, 'runOptimization': run,
            'sourcePinsSHA256': sha(self.root / 'Configuration/sh4-source.json'),
            'biosCopyConfigurationSHA256': sha(self.root / 'Configuration/sh4-bios-copies.json'),
            'mediaIdentitySHA256': sha(self.root / 'Configuration/media.json')}
        self.json(self.gen_path, self.generation)
        objects = {}
        for name in ('executor.o', 'game-targets.o', 'bios-targets.o', 'addrspace.o'):
            path = self.root / 'build/native/cpu/sh4' / name
            self.write(path, 'object fixture ' + name)
            objects[str(path.relative_to(self.root))] = sha(path)
        cpu_run = copy.deepcopy(run)
        for field in RUN_PATHS:
            cpu_run[field] = str((self.generated / run[field]).relative_to(self.root))
        cpu_run['mappedFetch']['replacementObject'] = 'build/native/cpu/sh4/addrspace.o'
        source_pins = {str((self.generated / p).relative_to(self.root)): h for p, h in generated_pins.items()}
        source_pins[str((self.corrected / self.address).relative_to(self.root))] = sha(self.corrected / self.address)
        source_pins[str((self.corrected / self.header).relative_to(self.root))] = sha(self.corrected / self.header)
        source_pins.update(run['generatorSources'])
        source_pins.update(run['templateSources'])
        source_pins['Configuration/sh4-blocks.json'] = run['configurationSHA256']
        self.cpu = {'generatedManifestSHA256': sha(self.gen_path), 'objects': objects,
            'sources': {'scripts/compile_sh4.py': sha(self.root / 'scripts/compile_sh4.py')},
            'sourcePins': source_pins, 'replacements': {
                INTERPRETER: [p for p in objects if not p.endswith('/addrspace.o')],
                ADDRESS_TARGET: ['build/native/cpu/sh4/addrspace.o']},
            'removedOriginalObjects': [OPCODES], 'runOptimization': cpu_run}
        self.json(self.cpu_path, self.cpu)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def json(self, path, value):
        self.write(path, json.dumps(value))

    def audit(self):
        return audit_cpu_inputs(self.root, {'sh4'})

    def save_cpu(self):
        self.json(self.cpu_path, self.cpu)

    def save_generation(self):
        self.json(self.gen_path, self.generation)
        self.cpu['generatedManifestSHA256'] = sha(self.gen_path)
        self.save_cpu()

    def rejected(self, message=None):
        return self.assertRaisesRegex(RuntimeError, message) if message else self.assertRaises(RuntimeError)

    def test_valid_pristine_and_corrected_headers_are_distinct(self):
        audit = self.audit()
        for tree in (self.original, self.corrected):
            self.assertEqual(audit['inputFiles'][str((tree / self.header).relative_to(self.root))], sha(tree / self.header))

    def test_changed_assembly_rejected(self):
        self.write(self.generated / 'game-targets.s', 'changed')
        with self.rejected('changed build input'):
            self.audit()

    def test_missing_assembly_rejected(self):
        (self.generated / 'bios-targets.s').unlink()
        with self.assertRaises(FileNotFoundError):
            self.audit()

    def test_changed_generated_header_rejected(self):
        self.write(self.generated / 'overlay/cycles.h', 'changed')
        with self.rejected():
            self.audit()

    def test_generated_assembly_must_have_compiler_pin(self):
        del self.cpu['sourcePins']['build/generated/sh4/game-targets.s']
        self.save_cpu()
        with self.rejected('compiler pin'):
            self.audit()

    def test_changed_template_rejected(self):
        self.write(self.root / 'Sources/Translated/sh4/templates/mapped_fetch.h', 'changed')
        with self.rejected():
            self.audit()

    def test_changed_configuration_rejected(self):
        self.write(self.root / 'Configuration/sh4-blocks.json', '{"changed":true}')
        with self.rejected():
            self.audit()

    def test_rebuilt_cpu_cannot_bless_changed_generation_configuration(self):
        for relative in ('Configuration/sh4-source.json', 'Configuration/sh4-bios-copies.json', 'Configuration/media.json'):
            with self.subTest(configuration=relative):
                path = self.root / relative
                before = path.read_bytes()
                self.write(path, '{"changed":true}')
                self.cpu['sourcePins'][relative] = sha(path)
                self.save_cpu()
                with self.rejected():
                    self.audit()
                path.write_bytes(before)
                del self.cpu['sourcePins'][relative]
                self.save_cpu()

    def test_changed_generator_rejected(self):
        self.write(self.root / 'scripts/compile_sh4.py', 'changed')
        with self.rejected():
            self.audit()

    def test_changed_generator_helper_rejected(self):
        self.write(self.root / 'scripts/compile_sh4_blocks.py', 'changed helper')
        with self.rejected():
            self.audit()

    def test_recompiled_cpu_cannot_bless_stale_generator_helper(self):
        path = self.root / 'scripts/validate_sh4_blocks.py'
        self.write(path, 'changed helper')
        self.cpu['sourcePins']['scripts/validate_sh4_blocks.py'] = sha(path)
        self.save_cpu()
        with self.rejected():
            self.audit()

    def test_missing_generation_helper_identity_rejected(self):
        del self.generation['runOptimization']['generatorSources']
        del self.cpu['runOptimization']['generatorSources']
        self.save_generation()
        with self.rejected('generation-time'):
            self.audit()

    def test_optional_mapped_read32_header_cannot_be_unbound(self):
        self.cpu['runOptimization']['mappedRead32Header'] = 'build/generated/sh4/not-bound.h'
        self.save_cpu()
        with self.rejected('mappedRead32Header'):
            self.audit()

    def add_counter_inputs(self):
        for field, relative in (('counterLoopHeader', 'counter_loop.h'),
                                ('counterLoopSource', 'counter_loop.cpp')):
            path = self.generated / relative
            self.write(path, 'counter fixture ' + relative)
            key = str(path.relative_to(self.root))
            self.generation['generated'][relative] = sha(path)
            self.generation['runOptimization'][field] = relative
            self.cpu['runOptimization'][field] = key
            self.cpu['sourcePins'][key] = sha(path)
        self.save_generation()

    def test_optional_counter_pair_is_content_bound(self):
        self.add_counter_inputs()
        audit = self.audit()
        for relative in ('counter_loop.h', 'counter_loop.cpp'):
            key = 'build/generated/sh4/' + relative
            self.assertEqual(audit['inputFiles'][key], sha(self.generated / relative))

    def test_counter_metadata_requires_both_path_roles(self):
        for metadata in (self.cpu, self.generation):
            with self.subTest(manifest='cpu' if metadata is self.cpu else 'generation'):
                self.add_counter_inputs()
                del metadata['runOptimization']['counterLoopSource']
                self.save_generation()
                with self.rejected('counterLoopSource'):
                    self.audit()
        self.add_counter_inputs()
        for metadata in (self.cpu, self.generation):
            del metadata['runOptimization']['counterLoopHeader']
            del metadata['runOptimization']['counterLoopSource']
            metadata['runOptimization']['counterLoop'] = {'reviewed': True}
        self.save_generation()
        with self.rejected('counterLoopHeader'):
            self.audit()

    def test_counter_roles_cannot_name_unindexed_pinned_input(self):
        self.add_counter_inputs()
        self.cpu['runOptimization']['counterLoopHeader'] = str((self.corrected / self.header).relative_to(self.root))
        self.generation['runOptimization']['counterLoopHeader'] = str(self.corrected / self.header)
        self.save_generation()
        with self.rejected('counterLoopHeader'):
            self.audit()

    def test_counter_roles_cannot_disagree_between_manifests(self):
        self.add_counter_inputs()
        self.cpu['runOptimization']['counterLoopHeader'] = 'build/generated/sh4/counter_loop.cpp'
        self.save_cpu()
        with self.rejected('counterLoopHeader'):
            self.audit()

    def test_counter_tamper_not_blessed_by_rebuilt_cpu(self):
        self.add_counter_inputs()
        path = self.generated / 'counter_loop.cpp'
        self.write(path, 'changed counter body')
        self.cpu['sourcePins'][str(path.relative_to(self.root))] = sha(path)
        self.save_cpu()
        with self.rejected():
            self.audit()

    def test_counter_input_requires_compiler_pin(self):
        self.add_counter_inputs()
        del self.cpu['sourcePins']['build/generated/sh4/counter_loop.h']
        self.save_cpu()
        with self.rejected('compiler pin'):
            self.audit()

    def add_pure_inputs(self):
        relative = 'pure_segments.h'
        path = self.generated / relative
        self.write(path, 'pure fixture')
        key = str(path.relative_to(self.root))
        self.generation['generated'][relative] = sha(path)
        self.generation['runOptimization']['pureSegmentsHeader'] = relative
        self.cpu['runOptimization']['pureSegmentsHeader'] = key
        self.cpu['sourcePins'][key] = sha(path)
        helper = 'scripts/compile_sh4_pure.py'
        self.write(self.root / helper, 'pure generator fixture')
        for metadata in (self.cpu, self.generation):
            metadata['runOptimization']['generatorSources'][helper] = sha(self.root / helper)
        self.cpu['sourcePins'][helper] = sha(self.root / helper)
        self.save_generation()

    def test_pure_header_bound_to_generated_inventory(self):
        self.add_pure_inputs()
        self.assertIn('build/generated/sh4/pure_segments.h', self.audit()['inputFiles'])
        self.write(self.generated / 'pure_segments.h', 'changed')
        with self.rejected():
            self.audit()

    def test_pure_metadata_requires_header_and_generator(self):
        self.add_pure_inputs()
        del self.cpu['runOptimization']['pureSegmentsHeader']
        self.save_cpu()
        with self.rejected('pureSegmentsHeader'):
            self.audit()
        self.add_pure_inputs()
        for metadata in (self.cpu, self.generation):
            del metadata['runOptimization']['generatorSources']['scripts/compile_sh4_pure.py']
        self.save_generation()
        with self.rejected('generator identity'):
            self.audit()

    def test_changed_generation_manifest_rejected(self):
        self.write(self.gen_path, '{}')
        with self.rejected('Changed manifest'):
            self.audit()

    def test_changed_cpu_manifest_rejected_by_engine_pin(self):
        digest = sha(self.cpu_path)
        self.cpu['extra'] = True
        self.save_cpu()
        with self.rejected('Changed manifest'):
            audit_cpu_inputs(self.root, {'sh4'}, {'sh4': digest})

    def test_changed_original_header_rejected_even_if_cpu_pin_rewritten(self):
        path = self.corrected / self.header
        self.write(path, 'unauthenticated original change')
        self.cpu['sourcePins'][str(path.relative_to(self.root))] = sha(path)
        self.save_cpu()
        with self.rejected('pinned inventory'):
            self.audit()

    def test_replacement_object_cannot_be_listed_twice(self):
        self.cpu['replacements'][INTERPRETER].append(self.cpu['replacements'][INTERPRETER][0])
        self.save_cpu()
        with self.rejected('exactly one'):
            self.audit()

    def test_unused_audited_object_rejected(self):
        self.cpu['replacements'][INTERPRETER].pop()
        self.save_cpu()
        with self.rejected('exactly one'):
            self.audit()

    def test_unaudited_replacement_object_rejected(self):
        self.cpu['replacements'][INTERPRETER].append('build/native/cpu/sh4/not-audited.o')
        self.save_cpu()
        with self.rejected('exactly one'):
            self.audit()

    def test_removal_and_replacement_must_be_disjoint(self):
        self.cpu['removedOriginalObjects'].append(INTERPRETER)
        self.save_cpu()
        with self.rejected('disjoint'):
            self.audit()

    def test_duplicate_removals_rejected(self):
        self.cpu['removedOriginalObjects'].append(OPCODES)
        self.save_cpu()
        with self.rejected('unique'):
            self.audit()

    def test_mapped_fetch_requires_addrspace_substitution(self):
        obj = self.cpu['replacements'].pop(ADDRESS_TARGET)
        self.cpu['replacements'][INTERPRETER] += obj
        self.save_cpu()
        with self.rejected('replace addrspace exactly once'):
            self.audit()

    def test_derived_source_inverse_is_verified_independently(self):
        path = self.generated / 'addrspace.cpp'
        self.write(path, path.read_text() + '// extra unaudited semantic change\n')
        digest = sha(path)
        self.generation['generated']['addrspace.cpp'] = digest
        self.cpu['sourcePins'][str(path.relative_to(self.root))] = digest
        self.save_generation()
        with self.rejected('transformation differs'):
            self.audit()

    def add_link_layout(self):
        for field, relative in (('linkOrderFile', 'hot-functions.order'),
                                ('linkResolutionFile', 'layout-resolution.json')):
            path = self.generated / relative
            self.write(path, 'layout fixture ' + relative)
            key = str(path.relative_to(self.root))
            self.generation['generated'][relative] = sha(path)
            self.generation['runOptimization'][field] = relative
            self.cpu['runOptimization'][field] = key
            self.cpu['sourcePins'][key] = sha(path)
        self.save_generation()
        return '-Wl,-order_file,' + str(self.generated / 'hot-functions.order')

    def test_link_layout_is_bound_and_exactly_once(self):
        argument = self.add_link_layout()
        audit = self.audit()
        args = [*audit['objects'], argument]
        validate_link_inventory(self.root, args, audit, self.reference)
        pins = link_input_files(self.root, args, self.reference)
        self.assertEqual(pins['build/generated/sh4/hot-functions.order'], sha(self.generated / 'hot-functions.order'))
        for invalid in (list(audit['objects']), [*args, argument],
                        [*audit['objects'], argument.replace('hot-functions.order', 'other.order')]):
            with self.rejected('authenticated link order'):
                validate_link_inventory(self.root, invalid, audit, self.reference)

    def test_link_layout_requires_generated_resolution(self):
        self.add_link_layout()
        del self.cpu['runOptimization']['linkResolutionFile']
        self.save_cpu()
        with self.rejected('linkResolutionFile'):
            self.audit()

    def test_unbound_layout_argument_rejected(self):
        audit = self.audit()
        with self.rejected('Unaudited link order'):
            validate_link_inventory(self.root, [*audit['objects'], '-Wl,-order_file,unbound.order'], audit, self.reference)

    def test_valid_link_inventory(self):
        audit = self.audit()
        validate_link_inventory(self.root, list(audit['objects']), audit, self.reference)

    def test_original_addrspace_cannot_remain_in_link(self):
        audit = self.audit()
        with self.rejected('remains linked'):
            validate_link_inventory(self.root, [*audit['objects'], ADDRESS_TARGET], audit, self.reference)

    def test_duplicate_relative_link_alias_rejected(self):
        audit = self.audit()
        obj = next(iter(audit['objects']))
        relative_alias = '../native/cpu/sh4/' + Path(obj).name
        with self.rejected('exactly once'):
            validate_link_inventory(self.root, [*audit['objects'], relative_alias], audit, self.reference)

    def test_unlinked_object_rejected(self):
        audit = self.audit()
        with self.rejected('exactly once'):
            validate_link_inventory(self.root, list(audit['objects'])[1:], audit, self.reference)

    def test_source_change_after_initial_audit_rejected(self):
        audit = self.audit()
        self.write(self.generated / 'run_dispatch.h', 'changed after link began')
        with self.rejected('changed during operation'):
            verify_snapshot(self.root, audit['inputFiles'])

    def test_local_hardware_object_and_archive_drift_rejected(self):
        for relative in ('CMakeFiles/hardware.o', 'libhardware.a'):
            self.write(self.reference / relative, 'original object')
        pins = link_input_files(self.root, ['CMakeFiles/hardware.o', 'libhardware.a', '/System/Library/fixture.a'], self.reference)
        self.assertEqual(len(pins), 2)
        self.write(self.reference / 'libhardware.a', 'changed archive')
        with self.rejected('changed during operation'):
            verify_snapshot(self.root, pins)

    def test_packaging_checks_same_exact_audit(self):
        audit = self.audit()
        manifest = {'CPUManifests': audit['CPUManifests'], 'originalObjectsReplaced': audit['replacements'],
                    'originalObjectsRemoved': audit['removals'], 'replacementObjects': audit['objects'],
                    'CPUInputFiles': audit['inputFiles'], 'runOptimization': audit['runOptimization'],
                    'linkCommand': list(audit['objects'])}
        manifest = json.loads(json.dumps(manifest).replace(str(self.root), 'PROJECT'))
        match_engine_audit(self.root, manifest, audit)
        manifest['CPUInputFiles'].pop(next(iter(manifest['CPUInputFiles'])))
        with self.rejected('CPUInputFiles'):
            match_engine_audit(self.root, manifest, audit)

    @patch('native_provenance.subprocess.check_output')
    def test_measured_minimum_macos(self, output):
        output.return_value = ' platform MACOS\n minos 14.0\n'
        self.assertEqual(minimum_macos('fake'), '14.0')
        for value in (' platform MACOS\n minos 14.0.1\n', ' platform IOS\n minos 14.0\n',
                      ' platform MACOS\n minos 14.0\n minos 14.0\n'):
            output.return_value = value
            with self.rejected():
                minimum_macos('fake')


if __name__ == '__main__':
    unittest.main(verbosity=2)
