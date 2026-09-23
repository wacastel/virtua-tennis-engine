#!/usr/bin/env python3
"""Shared fail-closed input and link checks for fixed engines and app packaging.

Generation inputs and compiler inputs are deliberately distinct: the SH4
generator reads pristine headers, then emits the documented clock correction;
the hardware compile recipe uses the separately pinned corrected source tree.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

CPUS = {'sh4', 'arm7', 'aicadsp'}
ADDRESS_TARGET = 'CMakeFiles/flycast_libretro.dir/core/hw/mem/addrspace.cpp.o'
RUN_PATHS = ('plan', 'executorSource', 'runDispatchHeader', 'mappedFetchHeader',
             'derivedAddressSpaceSource')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local(root, value, base=None):
    root = Path(root).resolve()
    value = str(value)
    if value.startswith('PROJECT/'):
        value = value[len('PROJECT/'):]
        base = root
    path = Path(value)
    path = (path if path.is_absolute() else (base or root) / path).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError('Build input escapes the project: ' + str(path))
    return path


class Snapshot:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.pins = {}

    def add(self, value, expected=None, base=None):
        path = local(self.root, value, base)
        key = str(path.relative_to(self.root))
        if expected is not None and not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise RuntimeError('Invalid SHA256 for ' + key)
        previous = self.pins.get(key)
        if previous is not None:
            if expected is not None and previous != expected:
                raise RuntimeError('Conflicting input identities: ' + key)
            return path
        digest = sha(path)
        if expected is not None and digest != expected:
            raise RuntimeError('Missing or changed build input: ' + key)
        self.pins[key] = digest
        return path

    def read_json(self, value, expected=None, base=None):
        path = local(self.root, value, base)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected is not None and digest != expected:
            raise RuntimeError('Changed manifest: ' + str(path.relative_to(self.root)))
        key = str(path.relative_to(self.root))
        if key in self.pins and self.pins[key] != digest:
            raise RuntimeError('Manifest changed during validation: ' + key)
        self.pins[key] = digest
        return json.loads(data)

    def add_map(self, records, base=None):
        if not isinstance(records, dict):
            raise RuntimeError('Expected a path-to-SHA256 input map')
        for value, digest in records.items():
            self.add(value, digest, base)


def verify_snapshot(root, pins):
    """Re-read every pin after linking/staging; the initial hash cache is not used."""
    for value, digest in pins.items():
        if sha(local(root, value)) != digest:
            raise RuntimeError('Build input changed during operation: ' + value)


def reference_inventory(snapshot, directory, source, original_files=None, original_source=None):
    manifest = snapshot.read_json(directory / 'build-manifest.json')
    inventory = snapshot.read_json(directory / 'upstream-files.json',
                                   manifest['upstreamFileInventorySHA256'])
    expected = {}
    for base, tree in inventory.items():
        for name, digest in tree['files'].items():
            path = local(snapshot.root, name, source / base)
            previous = expected.setdefault(path, digest)
            if previous != digest:
                raise RuntimeError('Conflicting upstream inventory: ' + str(path))
    for patch in manifest.get('sourcePatches', []):
        path = local(snapshot.root, patch['path'], source)
        if original_files is None or original_files.get(local(snapshot.root, patch['path'], original_source)) != patch['originalSHA256']:
            raise RuntimeError('Unbound original source patch: ' + patch['path'])
        if expected.get(path) not in (patch['originalSHA256'], patch['derivedSHA256']):
            raise RuntimeError('Corrected source inventory differs: ' + patch['path'])
        expected[path] = patch['derivedSHA256']
    return manifest, expected


def validate_run_optimization(snapshot, cpu, generation, generated, replacements):
    run = cpu.get('runOptimization')
    generated_run = generation.get('runOptimization')
    if run is None and generated_run is None:
        if ADDRESS_TARGET in replacements:
            raise RuntimeError('Address-space replacement lacks Run provenance')
        return None
    if not isinstance(run, dict) or not isinstance(generated_run, dict):
        raise RuntimeError('Run optimization must be bound in both manifests')
    source_pins = {local(snapshot.root, p): h for p, h in cpu.get('sourcePins', {}).items()}
    generator_sources = generated_run.get('generatorSources', {})
    required_helpers = {'scripts/compile_sh4_blocks.py', 'scripts/validate_sh4_blocks.py'}
    if not required_helpers <= set(generator_sources) or not generated_run.get('templateSources'):
        raise RuntimeError('Run optimization lacks generation-time helper/template identities')
    extra_maps = ('configurationSources', 'pureSourcePins')
    for field in ('generatorSources', 'templateSources', 'configurationSHA256', *extra_maps):
        if run.get(field) != generated_run.get(field):
            raise RuntimeError('Run generation metadata differs: ' + field)
    generation_inputs = {}
    for records in (generator_sources, generated_run['templateSources'],
                    *(generated_run.get(field, {}) for field in extra_maps),
                    {'Configuration/sh4-blocks.json': generated_run['configurationSHA256']}):
        snapshot.add_map(records)
        for path, digest in records.items():
            canonical = local(snapshot.root, path)
            if source_pins.get(canonical) != digest:
                raise RuntimeError('Run generator input lacks a compiler pin: ' + path)
            previous = generation_inputs.setdefault(canonical, digest)
            if previous != digest:
                raise RuntimeError('Conflicting Run generator input: ' + path)
    for relative, digest in generation['generated'].items():
        if source_pins.get(local(snapshot.root, relative, generated)) != digest:
            raise RuntimeError('Optimized generated input lacks a compiler pin: ' + relative)
    fields = list(RUN_PATHS)
    if 'mappedRead32Header' in run or 'mappedRead32Header' in generated_run:
        fields.append('mappedRead32Header')
    counter_fields = ('counterLoopHeader', 'counterLoopSource')
    if any(field in metadata for metadata in (run, generated_run)
           for field in (*counter_fields, 'counterLoop')):
        fields.extend(counter_fields)
    if any(field in metadata for metadata in (run, generated_run)
           for field in ('pureSegmentsHeader', 'pureSegments')):
        fields.append('pureSegmentsHeader')
        if 'scripts/compile_sh4_pure.py' not in generator_sources:
            raise RuntimeError('Pure segments lack generator identity')
    if any(field in metadata for metadata in (run, generated_run)
           for field in ('linkOrderFile', 'linkResolutionFile', 'linkLayout')):
        fields.extend(('linkOrderFile', 'linkResolutionFile'))
        if run.get('linkLayout') != generated_run.get('linkLayout'):
            raise RuntimeError('Link layout generation metadata differs')
    generated_pins = {local(snapshot.root, relative, generated): digest
                      for relative, digest in generation['generated'].items()}
    for field in fields:
        if field not in run or field not in generated_run:
            raise RuntimeError('Missing Run optimization input: ' + field)
        path = local(snapshot.root, run[field])
        if (path != local(snapshot.root, generated_run[field], generated)
                or path not in generated_pins
                or source_pins.get(path) != generated_pins[path]):
            raise RuntimeError('Unbound Run optimization input: ' + field)
    mapped = run['mappedFetch']
    generated_mapped = generated_run['mappedFetch']
    for field in ('originalObjectTarget', 'originalSource', 'originalSourceSHA256',
                  'sourceInverseTransformVerified'):
        if mapped.get(field) != generated_mapped.get(field):
            raise RuntimeError('Mapped-fetch metadata differs: ' + field)
    if mapped.get('originalObjectTarget') != ADDRESS_TARGET or mapped.get('sourceInverseTransformVerified') is not True:
        raise RuntimeError('Unsupported address-space substitution')
    replacement = local(snapshot.root, mapped['replacementObject'])
    if replacements.get(ADDRESS_TARGET) != [str(replacement)]:
        raise RuntimeError('Mapped fetch must replace addrspace exactly once')
    if replacement.name != Path(generated_mapped['replacementObject']).name:
        raise RuntimeError('Mapped-fetch generated object identity differs')
    original = snapshot.add(mapped['originalSource'], mapped['originalSourceSHA256'],
                            snapshot.root / 'build/clock-source/flycast')
    if source_pins.get(original) != mapped['originalSourceSHA256']:
        raise RuntimeError('Original address-space source lacks a compiler pin')
    # Independently verify the sole visibility transformation, rather than
    # trusting a Boolean recorded by the generator.
    old = b'static void* memInfo_ptr[0x100];'
    new = b'__attribute__((visibility("hidden"))) void* memInfo_ptr[0x100];'
    before = original.read_bytes()
    after = local(snapshot.root, run['derivedAddressSpaceSource']).read_bytes()
    if before.count(old) != 1 or after != before.replace(old, new, 1):
        raise RuntimeError('Address-space transformation differs from the reviewed change')
    return run


def audit_cpu_inputs(root, cpus, expected_manifests=None):
    root = Path(root).resolve()
    cpus = set(cpus)
    if not cpus or not cpus <= CPUS:
        raise RuntimeError('Unknown or empty CPU selection')
    if expected_manifests is not None and set(expected_manifests) != cpus:
        raise RuntimeError('CPU manifest selection differs')
    snapshot = Snapshot(root)
    pristine = root / 'build/reference-source/flycast'
    corrected = root / 'build/clock-source/flycast'
    _, pristine_files = reference_inventory(snapshot, root / 'build/reference', pristine)
    clock, corrected_files = reference_inventory(snapshot, root / 'build/reference-clock', corrected,
                                                 pristine_files, pristine)
    if clock.get('sh4ClockHz') != 200000000 or clock.get('interpreterCycleMultiplier') != 1:
        raise RuntimeError('The corrected 200 MHz hardware reference is required')
    manifests, hashes, objects, replacements, removals, forbidden, run = {}, {}, {}, {}, [], [], {}
    for cpu in sorted(cpus):
        path = root / 'build/native/cpu' / cpu / 'manifest.json'
        m = snapshot.read_json(path, None if expected_manifests is None else expected_manifests[cpu])
        if m.get('runtimeOpcodeDecoder', False):
            raise RuntimeError('Runtime CPU decoder remains: ' + cpu)
        generated = root / 'build/generated' / cpu
        generation_path = generated / ('manifest.json' if cpu == 'sh4' else 'generation.json')
        if m.get('generationManifest') and local(root, m['generationManifest']) != generation_path:
            raise RuntimeError('Unexpected generation manifest path: ' + cpu)
        generation_hash = m.get('generatedManifestSHA256') if cpu == 'sh4' else m.get('generationSHA256')
        if not generation_hash:
            raise RuntimeError('Missing generation manifest identity: ' + cpu)
        g = snapshot.read_json(generation_path, generation_hash)
        snapshot.add('scripts/compile_' + cpu + '.py', g['generatorSHA256'])
        for field in ('sources', 'sourcePins', 'objects'):
            snapshot.add_map(m.get(field, {}))
        if cpu == 'sh4':
            for config, field in (('Configuration/sh4-source.json', 'sourcePinsSHA256'),
                                  ('Configuration/sh4-bios-copies.json', 'biosCopyConfigurationSHA256'),
                                  ('Configuration/media.json', 'mediaIdentitySHA256')):
                snapshot.add(config, g[field])
            snapshot.add_map(g['generated'], generated)
            for field in ('inputSources', 'headers'):
                snapshot.add_map(g[field], pristine)
            for field in ('inputSources', 'headerSources'):
                snapshot.add_map(m.get(field, {}), pristine)
        else:
            snapshot.add_map(g['sources'], generated)
            snapshot.add_map(g['inputPins'])
            snapshot.add_map(g['sourcePins'], corrected)
        current_objects = {str(local(root, p)): h for p, h in m['objects'].items()}
        if not current_objects or len(current_objects) != len(m['objects']) or set(objects) & set(current_objects):
            raise RuntimeError('Missing, duplicate or shared replacement objects: ' + cpu)
        current_replacements = {}
        for old, values in m['replacements'].items():
            if old in replacements or not old.startswith('CMakeFiles/flycast_libretro.dir/') or not old.endswith('.o'):
                raise RuntimeError('Invalid or duplicate replacement: ' + old)
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list) or not values:
                raise RuntimeError('Empty replacement: ' + old)
            current_replacements[old] = [str(local(root, value)) for value in values]
        assigned = Counter(p for values in current_replacements.values() for p in values)
        if set(assigned) != set(current_objects) or any(n != 1 for n in assigned.values()):
            raise RuntimeError('Each audited CPU object must replace exactly one original object: ' + cpu)
        if cpu == 'sh4':
            optimization = validate_run_optimization(snapshot, m, g, generated, current_replacements)
            if optimization is not None:
                run[cpu] = optimization
        objects.update(current_objects)
        replacements.update(current_replacements)
        removals.extend(m.get('removedOriginalObjects', []))
        forbidden.extend(m.get('forbiddenSymbols', []))
        manifests[cpu] = m
        hashes[cpu] = snapshot.pins[str(path.relative_to(root))]
    if len(removals) != len(set(removals)) or set(removals) & set(replacements):
        raise RuntimeError('Removal and replacement inventories must be unique and disjoint')
    for old in removals:
        if not old.startswith('CMakeFiles/flycast_libretro.dir/') or not old.endswith('.o'):
            raise RuntimeError('Invalid original-object removal: ' + old)
    # A source/header hash recorded by a CPU builder must also agree with the
    # original upstream inventory (plus the explicit clock patch).
    for relative, digest in snapshot.pins.items():
        path = root / relative
        inventory = pristine_files if path.is_relative_to(pristine) else corrected_files if path.is_relative_to(corrected) else None
        if inventory is not None and inventory.get(path) != digest:
            raise RuntimeError('Original source/header differs from its pinned inventory: ' + relative)
    return {'CPUManifests': hashes, 'manifests': manifests, 'objects': objects,
            'replacements': replacements, 'removals': removals, 'forbidden': forbidden,
            'runOptimization': run, 'inputFiles': snapshot.pins}


def validate_link_inventory(root, arguments, audit, reference):
    """Check final link inputs, including relative aliases of absolute objects."""
    linked = Counter(str(local(root, arg, reference)) for arg in arguments if arg.endswith('.o'))
    for obj in audit['objects']:
        if linked[obj] != 1:
            raise RuntimeError('Replacement object must be linked exactly once: ' + obj)
    for old in set(audit['replacements']) | set(audit['removals']):
        if linked[str(local(root, old, reference))]:
            raise RuntimeError('Original replaced/removed object remains linked: ' + old)
    expected_order = audit.get('runOptimization', {}).get('sh4', {}).get('linkOrderFile')
    orders = [arg[len('-Wl,-order_file,'):] for arg in arguments if arg.startswith('-Wl,-order_file,')]
    if expected_order is None:
        if orders:
            raise RuntimeError('Unaudited link order file')
    elif len(orders) != 1 or local(root, orders[0], reference) != local(root, expected_order):
        raise RuntimeError('Expected exactly one authenticated link order file')
    if any(arg == '-order_file' or arg.startswith('-Wl,-order_file=') for arg in arguments):
        raise RuntimeError('Unsupported alternate link order argument')


def link_input_files(root, arguments, reference):
    """Pin local objects, archives and layout; SDK frameworks are outside this boundary."""
    snapshot = Snapshot(root)
    for argument in arguments:
        if argument.startswith('-Wl,-order_file,'):
            snapshot.add(local(root, argument[len('-Wl,-order_file,'):], reference))
            continue
        if argument.endswith(('.o', '.a')):
            path = Path(argument.replace('PROJECT/', str(Path(root).resolve()) + '/'))
            path = (path if path.is_absolute() else Path(reference) / path).resolve()
            if path.is_relative_to(Path(root).resolve()):
                snapshot.add(path)
    return snapshot.pins


def match_engine_audit(root, manifest, audit):
    expected = {
        'CPUManifests': audit['CPUManifests'],
        'originalObjectsReplaced': audit['replacements'],
        'originalObjectsRemoved': audit['removals'],
        'replacementObjects': audit['objects'],
        'CPUInputFiles': audit['inputFiles'],
        'runOptimization': audit['runOptimization'],
    }
    # Serialized engine manifests use PROJECT instead of an absolute root.
    expected = json.loads(json.dumps(expected).replace(str(Path(root).resolve()), 'PROJECT'))
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise RuntimeError('Engine CPU provenance differs: ' + field)
    validate_link_inventory(root, manifest['linkCommand'], audit, Path(root) / 'build/reference-clock')


def minimum_macos(path):
    output = subprocess.check_output(['xcrun', 'vtool', '-show-build', str(path)], text=True)
    versions = re.findall(r'^\s*minos\s+(\d+(?:\.\d+){1,2})\s*$', output, re.M)
    platforms = re.findall(r'^\s*platform\s+(\S+)\s*$', output, re.M)
    if len(versions) != 1 or platforms != ['MACOS']:
        raise RuntimeError('Expected exactly one macOS build-version record: ' + output)
    value = tuple((list(map(int, versions[0].split('.'))) + [0, 0])[:3])
    if value > (14, 0, 0):
        raise RuntimeError('Deployment target exceeds macOS 14: ' + versions[0])
    return versions[0]
