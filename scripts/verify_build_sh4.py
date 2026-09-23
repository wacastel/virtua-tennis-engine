#!/usr/bin/env python3
"""Synthetic compile-plan/cache/profile checks; never invokes a compiler."""
import json
from pathlib import Path
import tempfile
import unittest
from build_sh4 import (add_run_inputs, assembly_command, cache_valid, dependency_paths,
                      normalized_run, profile_command, sha, units,
                      validate_cycle_profiles, validate_dependencies)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vt-sh4-build-plan-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.generated = self.root / 'build/generated/sh4'
        self.output = self.root / 'build/native/cpu/sh4'
        self.reference = self.root / 'build/reference-clock'
        self.source = self.generated / 'vt_sh4_executor.cpp'
        self.obj = self.output / 'vt_sh4_executor.o'
        self.command = ['clang++', '-arch', 'arm64', '-isysroot', '/SDK path',
                        '-mmacosx-version-min=14.0', '-std=gnu++17', '-DTARGET_NO_REC',
                        '-I/original', '-c', str(self.source), '-o', str(self.obj)]
        self.generation = {'generated': {n: 'hash' for n in (
            'vt_sh4_executor.cpp', 'vt_operations_000.cpp', 'vt_image_bios.cpp',
            'blocks_00.cpp', 'guarded_chain.cpp', 'counter_loop.cpp', 'addrspace.cpp',
            'game-targets.s', 'bios-targets.s')}, 'runOptimization': {'translationUnitRoles': {
                'vt_sh4_executor.cpp': 'ordinary', 'blocks_00.cpp': 'block',
                'guarded_chain.cpp': 'block', 'counter_loop.cpp': 'block',
                'addrspace.cpp': 'address-space', 'game-targets.s': 'assembly', 'bios-targets.s': 'assembly'}}}

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_unit_inventory_includes_assembly_and_counter_loop(self):
        result = units(self.generation)
        self.assertEqual(result['game-targets.s'], 'assembly')
        self.assertEqual(result['counter_loop.cpp'], 'block')
        self.assertEqual(result['vt_operations_000.cpp'], 'ordinary')

    def test_counter_run_paths_are_normalized_without_rewriting_proof(self):
        run = {'counterLoopHeader': 'counter_loop.h', 'counterLoopSource': 'counter_loop.cpp',
               'counterLoop': {'helperHeader': 'counter_loop.h', 'proof': 'unchanged'},
               'mappedFetch': {'replacementObject': 'addrspace.o'}}
        normalized = normalized_run(run, self.generated, self.output, self.root)
        self.assertEqual(normalized['counterLoopHeader'], 'build/generated/sh4/counter_loop.h')
        self.assertEqual(normalized['counterLoopSource'], 'build/generated/sh4/counter_loop.cpp')
        self.assertEqual(normalized['counterLoop'], run['counterLoop'])
        self.assertEqual(run['counterLoopSource'], 'counter_loop.cpp')

    def test_pure_and_layout_paths_are_normalized_without_rewriting_proof(self):
        run = {'pureSegmentsHeader': 'pure_segments.h', 'linkOrderFile': 'hot-functions.order',
               'linkResolutionFile': 'layout-resolution.json', 'mappedFetch': {'replacementObject': 'addrspace.o'},
               'pureSegments': {'proof': 'original'}, 'linkLayout': {'functionCount': 256},
               'configurationSources': {'Configuration/sh4-pure.json': 'abc'},
               'pureSourcePins': {'build/reference-source/original.cpp': 'def'}}
        normalized = normalized_run(run, self.generated, self.output, self.root)
        for field in ('pureSegmentsHeader', 'linkOrderFile', 'linkResolutionFile'):
            self.assertEqual(normalized[field], 'build/generated/sh4/' + run[field])
        for field in ('pureSegments', 'linkLayout', 'configurationSources', 'pureSourcePins'):
            self.assertEqual(normalized[field], run[field])

    def test_additional_generation_maps_receive_compiler_pins(self):
        class RecordingSnapshot:
            def __init__(self, root): self.root = root; self.maps = []; self.files = []
            def add_map(self, value): self.maps.append(value)
            def add(self, path, digest): self.files.append((path, digest))
        snapshot = RecordingSnapshot(self.root)
        run = {'generatorSources': {'scripts/generator.py': 'one'},
               'templateSources': {'Sources/template.h': 'two'},
               'configurationSources': {'Configuration/pure.json': 'three'},
               'pureSourcePins': {'build/reference-source/original.cpp': 'four'},
               'configurationSHA256': 'five',
               'mappedFetch': {'originalSource': 'core/addrspace.cpp', 'originalSourceSHA256': 'six'}}
        add_run_inputs(snapshot, run, self.root / 'build/clock-source/flycast')
        self.assertEqual(snapshot.maps, [run[k] for k in ('generatorSources', 'templateSources', 'configurationSources', 'pureSourcePins')])
        self.assertEqual(snapshot.files, [(self.root / 'Configuration/sh4-blocks.json', 'five'),
            (self.root / 'build/clock-source/flycast/core/addrspace.cpp', 'six')])

    def test_ordinary_operations_cannot_be_promoted_to_block_profile(self):
        for name in ('vt_sh4_executor.cpp', 'vt_operations_000.cpp', 'vt_image_bios.cpp'):
            with self.subTest(source=name):
                self.generation['runOptimization']['translationUnitRoles'][name] = 'block'
                with self.assertRaises(RuntimeError):
                    units(self.generation)
                self.generation['runOptimization']['translationUnitRoles'].pop(name)

    def test_block_cannot_use_ordinary_profile(self):
        self.generation['runOptimization']['translationUnitRoles']['blocks_00.cpp'] = 'ordinary'
        with self.assertRaises(RuntimeError):
            units(self.generation)

    def test_unknown_or_missing_assembly_role_rejected(self):
        self.generation['runOptimization']['translationUnitRoles'].pop('game-targets.s')
        with self.assertRaises(RuntimeError):
            units(self.generation)

    def test_object_name_collision_rejected(self):
        self.generation['generated']['game-targets.cpp'] = 'hash'
        with self.assertRaises(RuntimeError):
            units(self.generation)

    def test_assembly_command_has_no_cpp_flags(self):
        cmd = assembly_command(self.command, self.generated / 'game-targets.s', self.output / 'game-targets.o')
        self.assertNotIn('-std=gnu++17', cmd)
        self.assertNotIn('-DTARGET_NO_REC', cmd)
        self.assertIn('-mmacosx-version-min=14.0', cmd)
        self.assertEqual(cmd[cmd.index('-isysroot') + 1], '/SDK path')
        self.assertEqual(cmd[cmd.index('-x') + 1], 'assembler')

    def test_assembly_rejects_wrong_deployment(self):
        self.command[self.command.index('-mmacosx-version-min=14.0')] = '-mmacosx-version-min=27.0'
        with self.assertRaises(RuntimeError):
            assembly_command(self.command, self.source, self.obj)

    def test_address_recipe_gets_no_sh4_overlay(self):
        self.assertEqual(profile_command(self.command, 'address-space', self.generated, self.source, self.obj), self.command)

    def test_ordinary_and_block_include_profiles_are_distinct(self):
        ordinary = profile_command(self.command, 'ordinary', self.generated, self.source, self.obj)
        block = profile_command(self.command, 'block', self.generated, self.source, self.obj)
        self.assertFalse(any('block-overlay' in arg for arg in ordinary))
        self.assertLess(block.index('-I' + str(self.generated / 'block-overlay/core')),
                        block.index('-I' + str(self.generated / 'overlay/core')))

    def test_depfile_parser_preserves_escaped_spaces(self):
        path = self.output / 'unit.o.d'
        self.write(path, 'unit.o: ../generated/sh4/header\\ with\\ space.h \\\n /SDK/header.h\n')
        self.assertEqual(dependency_paths(path, self.reference, self.root), [self.generated / 'header with space.h'])

    def test_cache_binds_real_object_and_dependency_bytes(self):
        dep = self.generated / 'header.h'
        depfile = self.obj.with_suffix('.o.d')
        identity = self.obj.with_suffix('.identity.json')
        self.write(self.obj, 'valid object')
        self.write(dep, 'valid header')
        self.write(depfile, 'dependency recipe')
        info = {'schema': 1, 'inputKey': 'key', 'objectSHA256': sha(self.obj),
                'dependencies': {str(dep.relative_to(self.root)): sha(dep)},
                'dependencyFileSHA256': sha(depfile)}
        self.write(identity, json.dumps(info))
        self.assertTrue(cache_valid(identity, 'key', self.obj, depfile, self.root))
        self.write(self.obj, 'corrupt object')
        self.assertFalse(cache_valid(identity, 'key', self.obj, depfile, self.root))
        self.write(self.obj, 'valid object')
        self.write(dep, 'changed header')
        self.assertFalse(cache_valid(identity, 'key', self.obj, depfile, self.root))

    def test_legacy_stamp_is_not_an_object_proof(self):
        identity = self.obj.with_suffix('.identity.json')
        self.write(identity, 'old command-only stamp')
        self.assertFalse(cache_valid(identity, 'key', self.obj, None, self.root))

    def test_shared_cycle_helpers_must_be_identical_between_profiles(self):
        ordinary = self.generated / 'overlay/core/hw/sh4/sh4_cycles.h'
        block = self.generated / 'block-overlay/core/hw/sh4/sh4_cycles.h'
        text = 'class Sh4Cycles { void executeFixed() {} bool vtCounterLoopReady() const {} };'
        self.write(ordinary, text)
        self.write(block, text.replace('void executeFixed()', '__attribute__((always_inline)) void executeFixed()'))
        validate_cycle_profiles(self.generated)
        self.write(block, block.read_text().replace('bool vtCounterLoopReady() const {}', ''))
        with self.assertRaises(RuntimeError):
            validate_cycle_profiles(self.generated)

    def test_ordinary_depfile_rejects_block_or_mapped_data_headers(self):
        base = {self.generated / 'overlay/core/hw/sh4/sh4_cycles.h'}
        validate_dependencies('ordinary', self.source, base | {self.generated / 'mapped_fetch.h'}, self.generated, True)
        for extra in ('chain_integer_templates.h', 'mapped_read32.h', 'pure_segments.h', 'block-templates/vt_integer_templates.h'):
            with self.subTest(header=extra), self.assertRaises(RuntimeError):
                validate_dependencies('ordinary', self.source, base | {self.generated / extra}, self.generated, True)

    def test_block_depfile_requires_actual_block_cycle_header(self):
        with self.assertRaises(RuntimeError):
            validate_dependencies('block', self.generated / 'blocks_00.cpp',
                                  {self.generated / 'overlay/core/hw/sh4/sh4_cycles.h'}, self.generated, False)

    def test_chain_annotations_are_exclusive(self):
        deps = {self.generated / p for p in ('block-overlay/core/hw/sh4/sh4_cycles.h',
                'chain_integer_templates.h', 'chain_block_templates.h', 'chain_game_delays.h',
                'block-templates/vt_floating_templates.h', 'mapped_read32.h')}
        validate_dependencies('block', self.generated / 'guarded_chain.cpp', deps, self.generated, True)
        with self.assertRaises(RuntimeError):
            validate_dependencies('block', self.generated / 'blocks_00.cpp', deps, self.generated, True)

    def test_address_dependency_profile_rejects_generated_header(self):
        with self.assertRaises(RuntimeError):
            validate_dependencies('address-space', self.generated / 'addrspace.cpp',
                                  {self.generated / 'mapped_fetch.h'}, self.generated, False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
