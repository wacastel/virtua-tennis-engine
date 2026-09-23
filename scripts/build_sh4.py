#!/usr/bin/env python3
"""Compile authenticated fixed SH4 translation units with separate ABI profiles.

Pass --project-root for isolated source-only review copies.
No performance/prototype files are read by the production build path.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

INTERPRETER = 'core/hw/sh4/interpr/sh4_interpreter.cpp'
ADDRESS_SOURCE = 'core/hw/mem/addrspace.cpp'
PREFIX = 'CMakeFiles/flycast_libretro.dir/'
ROLES = {'ordinary', 'block', 'assembly', 'address-space'}
REMOVED = ['core/hw/sh4/interpr/sh4_opcodes.cpp', 'core/hw/sh4/interpr/sh4_fpu.cpp',
           'core/hw/sh4/sh4_opcode_list.cpp', 'core/hw/sh4/sh4_cycles.cpp']
FORBIDDEN = ['OpPtr', 'OpDesc', 'BuildOpcodeTables', 'Sh4Cycles::countCycles',
             'iNotImplemented(Sh4Context*, unsigned int)']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, path)


def units(generation):
    """Use the authenticated file index, never an uncontrolled source glob."""
    indexed = generation['generated']
    declared = generation.get('runOptimization', {}).get('translationUnitRoles', {})
    result, object_names = {}, set()
    for name in sorted(indexed):
        path = Path(name)
        if path.suffix not in ('.cpp', '.s'):
            continue
        if path.parent != Path('.'):
            raise RuntimeError('Unexpected nested translation unit: ' + name)
        role = declared.get(name, 'ordinary' if path.suffix == '.cpp' else None)
        if role not in ROLES or (role == 'assembly') != (path.suffix == '.s'):
            raise RuntimeError('Missing or invalid translation-unit role: ' + name)
        obj = path.stem + '.o'
        if obj in object_names:
            raise RuntimeError('Two sources would overwrite one object: ' + obj)
        object_names.add(obj)
        result[name] = role
    if not result or set(declared) - set(result):
        raise RuntimeError('Unknown or missing declared translation unit')
    if 'runOptimization' in generation:
        if result.get('addrspace.cpp') != 'address-space' or result.get('vt_sh4_executor.cpp') != 'ordinary':
            raise RuntimeError('Run executor/address-space compiler profile changed')
        for name in ('game-targets.s', 'bios-targets.s'):
            if result.get(name) != 'assembly':
                raise RuntimeError('Missing Run target assembly: ' + name)
        for name, role in result.items():
            optimized = name.startswith(('blocks_', 'bios_blocks_')) or name in ('run_slot_branches.cpp', 'guarded_chain.cpp', 'counter_loop.cpp')
            if optimized != (role == 'block'):
                raise RuntimeError('Only approved optimized TUs may use the block-only profile: ' + name)
    if any(role == 'address-space' and name != 'addrspace.cpp' for name, role in result.items()):
        raise RuntimeError('Unexpected address-space translation unit')
    return result


def assembly_command(original, source, obj):
    """Preserve original compiler, SDK, architecture and deployment target only."""
    result = [original[0]]
    for flag in ('-arch', '-isysroot', '-target'):
        indexes = [i for i, value in enumerate(original) if value == flag]
        if len(indexes) > 1:
            raise RuntimeError('Ambiguous compiler target option: ' + flag)
        if indexes:
            i = indexes[0]
            result.extend(original[i:i+2])
    deployment = [x for x in original if x.startswith('-mmacosx-version-min=')]
    if len(deployment) != 1 or deployment[0].split('=', 1)[1] not in ('14.0', '14.0.0'):
        raise RuntimeError('Original compiler deployment target changed')
    if '-arch' not in result or result[result.index('-arch') + 1] != 'arm64':
        raise RuntimeError('Original compiler architecture changed')
    return result + deployment + ['-x', 'assembler', '-c', str(source), '-o', str(obj)]


def profile_command(original, role, generated, source, obj):
    if role == 'assembly':
        return assembly_command(original, source, obj)
    args = original.copy()
    if role == 'address-space':
        return args  # Original addrspace.cpp recipe: no SH4 overlays.
    roots = [generated / 'overlay']
    if role == 'block':
        roots.insert(0, generated / 'block-overlay')
    includes = []
    for root in roots:
        includes += ['-I' + str(root / 'core'), '-iquote', str(root / 'core/hw/sh4/interpr'),
                     '-iquote', str(root / 'core/hw/sh4')]
    includes.append('-I' + str(generated))
    args[1:1] = includes
    return args


def dependency_paths(depfile, reference, root):
    text = depfile.read_text().replace('\\\n', ' ')
    if ':' not in text:
        raise RuntimeError('Malformed compiler dependency file: ' + str(depfile))
    result = set()
    for token in shlex.split(text.split(':', 1)[1]):
        path = Path(token)
        path = (path if path.is_absolute() else reference / path).resolve()
        if path.is_relative_to(root):
            result.add(path)
    return sorted(result)


def validate_cycle_profiles(generated):
    ordinary = (generated / 'overlay/core/hw/sh4/sh4_cycles.h').read_text()
    block = (generated / 'block-overlay/core/hw/sh4/sh4_cycles.h').read_text()
    before = 'void executeFixed()'
    after = '__attribute__((always_inline)) void executeFixed()'
    if ordinary.count(before) != 1 or block != ordinary.replace(before, after, 1):
        raise RuntimeError('Block and ordinary cycle profiles differ beyond the reviewed annotation')


def validate_dependencies(role, source, dependencies, generated, mapped_read32):
    """Check what Clang actually included, not just the intended -I ordering."""
    generated = generated.resolve()
    paths = {p.resolve() for p in dependencies}
    local = {str(p.relative_to(generated)) for p in paths if p.is_relative_to(generated)}
    cycle = 'core/hw/sh4/sh4_cycles.h'
    if role == 'assembly':
        return
    if role == 'address-space':
        if local - {str(source.relative_to(generated))}:
            raise RuntimeError('Original addrspace recipe consumed a generated SH4 input')
        return
    expected_cycle = ('block-overlay/' if role == 'block' else 'overlay/') + cycle
    actual_cycles = {p for p in paths if str(p).endswith('/' + cycle)}
    if actual_cycles != {generated / expected_cycle}:
        raise RuntimeError('Wrong actual SH4 cycle-header profile: ' + source.name)
    chain = {p for p in local if Path(p).name.startswith('chain_')}
    if role == 'ordinary':
        forbidden = {'block_templates.h', 'exact_game_delays.h', 'mapped_read32.h', 'pure_segments.h'}
        if source.name != 'vt_sh4_executor.cpp':
            forbidden |= {'run_dispatch.h', 'mapped_fetch.h'}
        if chain or any(p.startswith(('block-overlay/', 'block-templates/')) or p in forbidden for p in local):
            raise RuntimeError('Ordinary SH4 TU consumed optimized-only headers: ' + source.name)
    elif source.name == 'guarded_chain.cpp':
        if chain != {'chain_integer_templates.h', 'chain_block_templates.h', 'chain_game_delays.h'}:
            raise RuntimeError('Guarded chain did not use its explicit annotation headers')
        if {'vt_integer_templates.h', 'block-templates/vt_integer_templates.h'} & local:
            raise RuntimeError('Guarded chain also consumed an unannotated integer template')
    elif chain:
        raise RuntimeError('Chain annotations leaked into another block TU: ' + source.name)
    if role == 'block' and mapped_read32 and source.name != 'counter_loop.cpp':
        required = {'block-templates/vt_floating_templates.h', 'mapped_read32.h'}
        if source.name != 'guarded_chain.cpp':
            required.add('block-templates/vt_integer_templates.h')
        if not required <= local or {'vt_integer_templates.h', 'vt_floating_templates.h'} & local:
            raise RuntimeError('Block-only read32 template profile differs: ' + source.name)


def cache_valid(identity, key, obj, depfile, root):
    """A matching command stamp alone cannot bless replaced/corrupt object bytes."""
    try:
        info = json.loads(identity.read_text())
        if info.get('schema') != 1 or info.get('inputKey') != key or sha(obj) != info.get('objectSHA256'):
            return False
        if depfile is not None and sha(depfile) != info.get('dependencyFileSHA256'):
            return False
        return all(sha(root / p) == digest for p, digest in info['dependencies'].items())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def normalized_run(run, generated, output, root):
    value = json.loads(json.dumps(run))
    for field in ('plan', 'executorSource', 'runDispatchHeader', 'mappedFetchHeader',
                  'derivedAddressSpaceSource', 'mappedRead32Header',
                  'counterLoopHeader', 'counterLoopSource', 'pureSegmentsHeader',
                  'linkOrderFile', 'linkResolutionFile'):
        if field in value:
            value[field] = str((generated / value[field]).relative_to(root))
    value['mappedFetch']['replacementObject'] = str((output / value['mappedFetch']['replacementObject']).relative_to(root))
    return value


def add_run_inputs(snapshot, run, source):
    """Pin generation-time config and pure-body inputs alongside all helpers."""
    for field in ('generatorSources', 'templateSources'):
        snapshot.add_map(run[field])
    for field in ('configurationSources', 'pureSourcePins'):
        if field in run:
            snapshot.add_map(run[field])
    snapshot.add(snapshot.root / 'Configuration/sh4-blocks.json', run['configurationSHA256'])
    snapshot.add(source / run['mappedFetch']['originalSource'], run['mappedFetch']['originalSourceSHA256'])


def build(root, generated, output, jobs, plan_only=False):
    root, generated, output = [Path(p).resolve() for p in (root, generated, output)]
    for path in (generated, output):
        if not path.is_relative_to(root):
            raise RuntimeError('Generated files and objects must stay inside the project')
    sys.path.insert(0, str(root / 'scripts'))
    sys.path.insert(0, str(root / 'Tools/ReferenceLab'))
    from build_observer import compile_command, REFERENCE, SOURCE
    from build_arm7 import dependency_pins
    from native_provenance import Snapshot, local, reference_inventory, verify_snapshot, minimum_macos
    if SOURCE.resolve() != root / 'build/clock-source/flycast' or REFERENCE.resolve() != root / 'build/reference-clock':
        raise RuntimeError('The original compile helper belongs to a different project')
    snapshot = Snapshot(root)
    generation = snapshot.read_json(generated / 'manifest.json')
    snapshot.add(root / 'scripts/compile_sh4.py', generation['generatorSHA256'])
    snapshot.add(__file__)
    snapshot.add(root / 'scripts/build_arm7.py')
    snapshot.add(root / 'scripts/native_provenance.py')
    snapshot.add(root / 'Tools/ReferenceLab/build_observer.py')
    snapshot.add(root / 'Sources/Bridge/vt_fixed_fault.h')
    pristine = root / 'build/reference-source/flycast'
    _, pristine_files = reference_inventory(snapshot, root / 'build/reference', pristine)
    clock, corrected_files = reference_inventory(snapshot, REFERENCE, SOURCE, pristine_files, pristine)
    if clock.get('sh4ClockHz') != 200000000 or clock.get('interpreterCycleMultiplier') != 1:
        raise RuntimeError('The corrected 200 MHz reference is required')
    snapshot.add_map(generation['inputSources'], pristine)
    snapshot.add_map(generation['headers'], pristine)
    snapshot.add_map(generation['generated'], generated)
    for path, field in (('Configuration/sh4-source.json', 'sourcePinsSHA256'),
                        ('Configuration/sh4-bios-copies.json', 'biosCopyConfigurationSHA256'),
                        ('Configuration/media.json', 'mediaIdentitySHA256')):
        snapshot.add(root / path, generation[field])
    run = generation.get('runOptimization')
    if run is not None:
        add_run_inputs(snapshot, run, SOURCE)
    roles = units(generation)
    if run:
        validate_cycle_profiles(generated)
    originals = [INTERPRETER] + ([ADDRESS_SOURCE] if run else [])
    snapshot.add_map({str(p.relative_to(root)): h for p, h in dependency_pins(originals).items()})
    for rel, digest in snapshot.pins.items():
        path = root / rel
        inventory = pristine_files if path.is_relative_to(pristine) else corrected_files if path.is_relative_to(SOURCE) else None
        if inventory is not None and inventory.get(path) != digest:
            raise RuntimeError('Changed authenticated original input: ' + rel)
    # Bind helper and input metadata before retrieving any compiler recipe.
    work, commands = [], {}
    for name, role in roles.items():
        source = generated / name
        obj = output / (source.stem + '.o')
        original = ADDRESS_SOURCE if role == 'address-space' else INTERPRETER
        args, target = compile_command(original, source, obj)
        args = profile_command(args, role, generated, source, obj)
        key = str(obj.relative_to(root))
        commands[key] = {'source': str(source.relative_to(root)), 'role': role,
                         'originalTarget': target, 'command': [x.replace(str(root), 'PROJECT') for x in args]}
        cache_key = hashlib.sha256(json.dumps({'command': args, 'inputs': snapshot.pins}, sort_keys=True).encode()).hexdigest()
        work.append((args, obj, role, cache_key))
    output.mkdir(parents=True, exist_ok=True)
    if plan_only:
        verify_snapshot(root, snapshot.pins)
        save(output / 'compile-plan.json', {'reviewOnly': True, 'commands': commands, 'sourcePins': snapshot.pins})
        return

    def compile_one(item):
        args, obj, role, key = item
        depfile = None if role == 'assembly' else obj.with_suffix('.o.d')
        identity = obj.with_suffix('.identity.json')
        if not cache_valid(identity, key, obj, depfile, root):
            with obj.with_suffix('.log').open('w') as log:
                subprocess.run(args, cwd=REFERENCE, stdout=log, stderr=subprocess.STDOUT, check=True)
        paths = [] if depfile is None else dependency_paths(depfile, REFERENCE, root)
        validate_dependencies(role, Path(args[args.index('-c') + 1]), paths, generated,
                              bool(run and run.get('mappedRead32Header')))
        actual = {}
        for path in paths:
            digest = sha(path)
            rel = str(path.relative_to(root))
            if path.is_relative_to(SOURCE):
                expected = corrected_files.get(path)
            elif path.is_relative_to(pristine):
                expected = pristine_files.get(path)
            else:
                expected = snapshot.pins.get(rel)
            if expected is None or digest != expected:
                raise RuntimeError('Unexpected or changed compiler dependency: ' + rel)
            actual[rel] = digest
        digest = sha(obj)
        identity_data = {'schema': 1, 'inputKey': key, 'objectSHA256': digest,
                         'dependencies': actual, 'dependencyFileSHA256': None if depfile is None else sha(depfile)}
        save(identity, identity_data)
        return str(obj.relative_to(root)), digest, actual

    objects = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for name, digest, dependencies in pool.map(compile_one, work):
            objects[name] = digest
            snapshot.add_map(dependencies)
    verify_snapshot(root, snapshot.pins)
    symbols = subprocess.check_output(['/usr/bin/nm', '-C', *[str(root / p) for p in objects]], text=True)
    for banned in FORBIDDEN:
        if banned in symbols:
            raise RuntimeError('Original decoder remains in a fixed object: ' + banned)
    object_audits = {}
    for name in objects:
        path = root / name
        arch = subprocess.check_output(['/usr/bin/lipo', '-archs', str(path)], text=True).strip()
        if arch != 'arm64':
            raise RuntimeError('Wrong replacement architecture: ' + name)
        object_audits[name] = {'architecture': arch, 'minimumMacOS': minimum_macos(path)}
    replacements = {PREFIX + INTERPRETER + '.o': [p for p in objects if Path(p).name != 'addrspace.o']}
    if run:
        replacements[PREFIX + ADDRESS_SOURCE + '.o'] = [str((output / 'addrspace.o').relative_to(root))]
    manifest = {'cpu': 'SH4', 'runtimeOpcodeDecoder': False, 'architecture': 'arm64',
                'minimumMacOS': max((x['minimumMacOS'] for x in object_audits.values()),
                                    key=lambda x: tuple(map(int, x.split('.')))),
                'generatedManifestSHA256': sha(generated / 'manifest.json'),
                'replacements': replacements, 'removedOriginalObjects': [PREFIX + p + '.o' for p in REMOVED],
                'objects': objects, 'objectAudits': object_audits,
                'commands': {p: row['command'] for p, row in commands.items()}, 'compilationUnits': commands,
                'sourcePins': snapshot.pins, 'inputSources': generation['inputSources'],
                'headerSources': generation['headers'], 'forbiddenSymbols': FORBIDDEN}
    if run:
        manifest['runOptimization'] = normalized_run(run, generated, output, root)
    verify_snapshot(root, snapshot.pins)
    for name, digest in objects.items():
        if sha(root / name) != digest:
            raise RuntimeError('Object changed during final audit: ' + name)
    save(output / 'manifest.json', manifest)
    print(json.dumps({'manifest': str((output / 'manifest.json').relative_to(root)), 'objects': len(objects)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--generated', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    root = args.project_root.resolve()
    build(root, args.generated or root / 'build/generated/sh4', args.output or root / 'build/native/cpu/sh4', args.jobs, args.plan_only)


if __name__ == '__main__':
    main()
