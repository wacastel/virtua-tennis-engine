#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Audit tagged-audio trial metadata without running an audio device.

Spec schema: {"recipes":[{"name":"...","path":"relative/recipe.json",
"trials":[{"label":"original","directory":"relative/trial",
"manifest":"relative/build/manifest.json"}, ...]}]}. Paths are project-relative.
The first trial is the unchanged control. A failed candidate is a valid result;
inconsistent evidence is an error. No counters or thresholds are relaxed.
"""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def relative(path):
    return str(path.resolve().relative_to(ROOT))


def little_endian_array(data, typecode, stereo_frame_bytes):
    if len(data) % stereo_frame_bytes:
        raise ValueError('PCM does not contain complete stereo frames')
    result = array(typecode)
    result.frombytes(data)
    if sys.byteorder != 'little':
        result.byteswap()
    return result


def verify_submitted_pcm(data):
    samples = little_endian_array(data, 'h', 4)
    count = len(samples) // 2
    for frame in range(count):
        if samples[frame * 2] != (frame & 0x3fff) + 1 or samples[frame * 2 + 1] != ((frame >> 14) & 0x3fff) + 1:
            raise ValueError(f'Submitted source tag is not the expected frame ID at {frame}')
    return count


def decode_tap(data, sent):
    """Reconstruct the harness's exact tag and zero-run evidence from PCM bytes."""
    samples = little_endian_array(data, 'f', 8)
    frames = len(samples) // 2
    zero_frames = invalid = duplicate = reverse = missing = 0
    first = last = zero_start = None
    transitions = []

    def finish_zero(end):
        nonlocal zero_start
        if zero_start is not None:
            transitions.append({'kind': 'zeroRun', 'firstCapturedFrame': zero_start,
                                'frameCount': end - zero_start})
            zero_start = None

    for frame in range(frames):
        left, right = samples[frame * 2], samples[frame * 2 + 1]
        if left == 0 and right == 0:
            zero_frames += 1
            if zero_start is None:
                zero_start = frame
            continue
        finish_zero(frame)
        left, right = left * 32768, right * 32768
        if not (math.isfinite(left) and math.isfinite(right) and 1 <= left <= 16384 and 1 <= right <= 16384
                and abs(left - round(left)) < .01 and abs(right - round(right)) < .01):
            invalid += 1
            continue
        tag = (round(left) - 1) | ((round(right) - 1) << 14)
        if tag >= sent:
            invalid += 1
            continue
        if first is None:
            first = tag
        if last is not None and tag != last + 1:
            if tag == last:
                duplicate += 1
            elif tag < last:
                reverse += 1
            else:
                missing += tag - last - 1
            # Match the harness's bounded discontinuity list, while retaining full scalar counts.
            if len(transitions) < 5000:
                transitions.append({'kind': 'tagDiscontinuity', 'capturedFrame': frame,
                                    'previousTag': last, 'nextTag': tag, 'delta': tag - last})
        last = tag
    finish_zero(frames)
    scalars = {'capturedStereoFrames': frames, 'zeroFrames': zero_frames,
               'invalidTagFrames': invalid, 'duplicateTagFrames': duplicate, 'reverseTagFrames': reverse,
               'missingFramesBetweenValidTags': missing, 'firstTag': first, 'lastTag': last,
               'allSubmittedTagsObservedExactlyOnceInOrder': first == 0 and last == sent - 1 and
                   missing == duplicate == reverse == invalid == 0}
    return scalars, transitions


def verify_tap_metadata(data, sent, reported, transitions):
    scalars, decoded_transitions = decode_tap(data, sent)
    for key, value in scalars.items():
        if key not in reported or reported[key] != value:
            raise ValueError(f'Tap scalar {key} disagrees with independently decoded PCM')
    if transitions != decoded_transitions:
        raise ValueError('Tap transitions disagree with independently decoded PCM')
    return scalars, decoded_transitions


def verify_source_inventory(sources, pinned):
    if 'AudioOutput.swift' not in sources:
        raise ValueError('Required copied AudioOutput.swift is absent')
    if not ({'TaggedAudioBaseline.swift', 'TaggedAudioHarness.swift'} & sources.keys()):
        raise ValueError('Required copied harness source is absent')
    if any(pinned.get(name) != digest for name, digest in sources.items()):
        raise ValueError('Copied source identity mismatch')


def metrics(report, events, transitions, wanted):
    actual = [e for e in events if e['kind'] == 'enqueue']
    wanted = [e for e in wanted if e['kind'] == 'batch']
    if len(actual) != len(wanted) or any(e['batch'] != w['batch'] for e, w in zip(actual, wanted)):
        raise ValueError('Recipe and actual batch sequence differ')
    rate = report['sourceRate']
    tap = report['tap']
    zeros = [e for e in transitions if e['kind'] == 'zeroRun' and e['firstCapturedFrame'] > 0
             and e['firstCapturedFrame'] + e['frameCount'] < tap['capturedStereoFrames']]
    all_tags = (tap['firstTag'] == 0 and tap['lastTag'] == report['submittedStereoFrames'] - 1
                and all(tap[k] == 0 for k in ['invalidTagFrames', 'duplicateTagFrames',
                                             'reverseTagFrames', 'missingFramesBetweenValidTags'])
                and not tap['overflow'])
    lateness = [(e['arrivalSeconds'] - w['arrivalSeconds']) * 1000 for e, w in zip(actual, wanted)]
    return {'submittedStereoFrames': report['submittedStereoFrames'],
            'allSubmittedTagsObservedExactlyOnceInOrder': all_tags,
            'firstTag': tap['firstTag'], 'lastTag': tap['lastTag'],
            'missingFramesBetweenTags': tap['missingFramesBetweenValidTags'],
            'invalidTagFrames': tap['invalidTagFrames'], 'duplicateTagFrames': tap['duplicateTagFrames'],
            'reverseTagFrames': tap['reverseTagFrames'], 'captureOverflow': tap['overflow'],
            'interiorZeroGroups': len(zeros), 'interiorZeroFrames': [e['frameCount'] for e in zeros],
            'interiorZeroTotalMilliseconds': sum(e['frameCount'] for e in zeros) / rate * 1000,
            'interiorZeroMaximumMilliseconds': max([e['frameCount'] for e in zeros] or [0]) / rate * 1000,
            'flushCounts': report['finalAudio']['flushCounts'],
            'peakPendingFrames': report['finalAudio']['peakPendingFrames'],
            'minPendingAtEnqueueFrames': report['finalAudio']['minPendingFrames'],
            'producerLatenessMilliseconds': {'max': max(lateness), 'mean': sum(lateness) / len(lateness),
                                             'p95': sorted(lateness)[int(len(lateness) * .95)]}}


def gates(control, candidate):
    return {'completeExactSourceTags': candidate['allSubmittedTagsObservedExactlyOnceInOrder'],
            'noAdditionalGapGroups': candidate['interiorZeroGroups'] <= control['interiorZeroGroups'],
            'noGreaterMaximumInteriorSilence': candidate['interiorZeroMaximumMilliseconds'] <= control['interiorZeroMaximumMilliseconds'],
            'noGreaterTotalInteriorSilence': candidate['interiorZeroTotalMilliseconds'] <= control['interiorZeroTotalMilliseconds'],
            'noAdditionalBacklogFlushes': candidate['flushCounts'].get('backlog', 0) <= control['flushCounts'].get('backlog', 0)}


def trial(entry, recipe_path, recipe):
    directory, manifest_path = ROOT / entry['directory'], ROOT / entry['manifest']
    report_path = directory / 'report.json'
    report, manifest = read(report_path), read(manifest_path)
    if report['recipeSHA256'] != sha(recipe_path):
        raise ValueError('Recipe identity mismatch')
    pairs = [('submitted-s16le.pcm', report['inputPCM_SHA256']),
             ('producer-events.json', report['eventsSHA256']),
             ('player-tap-f32le.pcm', report['tap']['tapPCM_SHA256']),
             ('tap-blocks.json', report['tap']['tapMetadataSHA256'])]
    for name, expected in pairs:
        if sha(directory / name) != expected:
            raise ValueError(f'{name}: recorded identity mismatch')
    # Both initial lab manifests and the reusable builder retain exact source copies.
    sources = {name: sha(manifest_path.parent / name)
               for name in ['AudioOutput.swift', 'TaggedAudioBaseline.swift', 'TaggedAudioHarness.swift']
               if (manifest_path.parent / name).is_file()}
    pinned = manifest.get('copiedSources', {})
    pinned.update({Path(path).name: digest for path, digest in manifest.get('copiedSourceHashes', {}).items()})
    verify_source_inventory(sources, pinned)
    executable = manifest_path.parent / Path(manifest['executable']).name
    if sha(executable) != manifest['executableSHA256']:
        raise ValueError('Executable identity mismatch')
    sent = verify_submitted_pcm((directory / 'submitted-s16le.pcm').read_bytes())
    if sent != report['submittedStereoFrames'] or sent != sum(e['batch'] for e in recipe['events'] if e['kind'] == 'batch'):
        raise ValueError('Submitted PCM length disagrees with report or recipe')
    decoded, transitions = verify_tap_metadata((directory / 'player-tap-f32le.pcm').read_bytes(), sent,
                                               report['tap'], read(directory / 'tap-transitions.json'))
    tap_blocks = read(directory / 'tap-blocks.json')
    offset = 0
    for block in tap_blocks:
        if block['firstCapturedFrame'] != offset or block['frameCount'] <= 0 or block['sampleRate'] != report['sourceRate']:
            raise ValueError('Tap block coverage or sample rate is inconsistent')
        offset += block['frameCount']
    if offset != decoded['capturedStereoFrames'] or len(tap_blocks) != report['tap']['tapBlocks']:
        raise ValueError('Tap block count does not cover captured PCM')
    checked_report = dict(report, tap=dict(report['tap'], **decoded))
    result = metrics(checked_report, read(directory / 'producer-events.json'), transitions, recipe['events'])
    result.update({'label': entry['label'], 'report': relative(report_path), 'reportSHA256': sha(report_path),
                   'manifest': relative(manifest_path), 'manifestSHA256': sha(manifest_path),
                   'copiedSourceSHA256': sources, 'executableSHA256': manifest['executableSHA256'],
                   'inputPCM_SHA256': report['inputPCM_SHA256'], 'tapPCM_SHA256': report['tap']['tapPCM_SHA256'],
                   'tapTransitionsSHA256': sha(directory / 'tap-transitions.json'),
                   'PCMDecodedIndependently': True, 'submittedPCMSequenceIndependentlyVerified': True,
                   'decodedTapScalars': decoded, 'tapMetadataMatchesDecodedPCM': True,
                   'producerEventsSHA256': report['eventsSHA256'],
                   'exactInverseSourceChanges': manifest.get('replacements', []),
                   'inverseTransformVerified': manifest.get('inverseTransformVerified'), 'engineLinked': False})
    return result


def self_test():
    report = {'sourceRate': 1000, 'submittedStereoFrames': 100,
              'tap': {'firstTag': 0, 'lastTag': 99, 'capturedStereoFrames': 130,
                      'invalidTagFrames': 0, 'duplicateTagFrames': 0, 'reverseTagFrames': 0,
                      'missingFramesBetweenValidTags': 0, 'overflow': False},
              'finalAudio': {'flushCounts': {}, 'peakPendingFrames': 100, 'minPendingFrames': 5}}
    actual = [{'kind': 'enqueue', 'arrivalSeconds': .003, 'batch': 100}]
    wanted = [{'kind': 'batch', 'arrivalSeconds': 0, 'batch': 100}]
    zeros = [{'kind': 'zeroRun', 'firstCapturedFrame': 0, 'frameCount': 10},
             {'kind': 'zeroRun', 'firstCapturedFrame': 50, 'frameCount': 5},
             {'kind': 'zeroRun', 'firstCapturedFrame': 125, 'frameCount': 5}]
    base = metrics(report, actual, zeros, wanted)
    assert base['interiorZeroGroups'] == 1 and base['interiorZeroTotalMilliseconds'] == 5
    assert base['producerLatenessMilliseconds']['max'] == 3
    assert all(gates(base, base).values())
    missing_tail = json.loads(json.dumps(report)); missing_tail['tap']['lastTag'] = 98
    assert not metrics(missing_tail, actual, zeros, wanted)['allSubmittedTagsObservedExactlyOnceInOrder']
    missing_head = json.loads(json.dumps(report)); missing_head['tap']['firstTag'] = 1
    assert not metrics(missing_head, actual, zeros, wanted)['allSubmittedTagsObservedExactlyOnceInOrder']
    loss = dict(base, missingFramesBetweenTags=1, allSubmittedTagsObservedExactlyOnceInOrder=False)
    assert not gates(base, loss)['completeExactSourceTags']
    more = dict(base, interiorZeroGroups=2)
    assert not gates(base, more)['noAdditionalGapGroups']
    longer = dict(base, interiorZeroTotalMilliseconds=6, interiorZeroMaximumMilliseconds=6,
                  flushCounts={'backlog': 1})
    check = gates(base, longer)
    assert not check['noGreaterMaximumInteriorSilence'] and not check['noGreaterTotalInteriorSilence']
    assert not check['noAdditionalBacklogFlushes']
    try:
        metrics(report, actual, zeros, [{'kind': 'batch', 'arrivalSeconds': 0, 'batch': 99}])
    except ValueError:
        pass
    else:
        raise AssertionError('Mismatched input was accepted')
    def expect_error(call):
        try:
            call()
        except ValueError:
            return
        raise AssertionError('Corrupt evidence was accepted')

    def float_pcm(tags):
        values = array('f')
        for tag in tags:
            values.extend([0, 0] if tag is None else [((tag & 0x3fff) + 1) / 32768,
                                                     (((tag >> 14) & 0x3fff) + 1) / 32768])
        if sys.byteorder != 'little':
            values.byteswap()
        return values.tobytes()

    data = float_pcm([None, 0, 1, None, 2, 3, None])
    decoded, transitions = decode_tap(data, 4)
    assert decoded['zeroFrames'] == 3 and decoded['capturedStereoFrames'] == 7
    assert decoded['firstTag'] == 0 and decoded['lastTag'] == 3
    assert decoded['allSubmittedTagsObservedExactlyOnceInOrder']
    assert verify_tap_metadata(data, 4, decoded, transitions) == (decoded, transitions)
    changed_transition = json.loads(json.dumps(transitions)); changed_transition[1]['frameCount'] = 2
    expect_error(lambda: verify_tap_metadata(data, 4, decoded, changed_transition))
    changed_tail = dict(decoded, lastTag=2, allSubmittedTagsObservedExactlyOnceInOrder=False)
    expect_error(lambda: verify_tap_metadata(data, 4, changed_tail, transitions))
    expect_error(lambda: verify_tap_metadata(float_pcm([None, 0, 1, None, 2, None]), 4, decoded, transitions))
    expect_error(lambda: decode_tap(data[:-1], 4))
    repeated, _ = decode_tap(float_pcm([0, 1, 1, 3]), 4)
    assert repeated['duplicateTagFrames'] == 1 and repeated['missingFramesBetweenValidTags'] == 1
    reverse, _ = decode_tap(float_pcm([0, 2, 1, 3]), 4)
    assert reverse['reverseTagFrames'] == 1 and not reverse['allSubmittedTagsObservedExactlyOnceInOrder']
    expect_error(lambda: verify_source_inventory({}, {}))
    expect_error(lambda: verify_source_inventory({'AudioOutput.swift': 'a'}, {'AudioOutput.swift': 'a'}))
    expect_error(lambda: verify_source_inventory({'TaggedAudioHarness.swift': 'b'}, {'TaggedAudioHarness.swift': 'b'}))
    source = {'AudioOutput.swift': 'a', 'TaggedAudioHarness.swift': 'b'}
    verify_source_inventory(source, source)
    expect_error(lambda: verify_source_inventory(source, dict(source, **{'AudioOutput.swift': 'changed'})))
    submitted = array('h', [1, 1, 2, 1])
    if sys.byteorder != 'little':
        submitted.byteswap()
    assert verify_submitted_pcm(submitted.tobytes()) == 2
    expect_error(lambda: verify_submitted_pcm(submitted.tobytes()[:-1]))
    print('PASS: 25 controls including independent PCM decode, modified transitions, false/truncated tail metadata, source presence/identity and submitted tags; no device run')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    if not args.spec or not args.out:
        p.error('--spec and --out are required')
    rows = []
    for item in read(args.spec)['recipes']:
        path = ROOT / item['path']; recipe = read(path)
        trials = [trial(entry, path, recipe) for entry in item['trials']]
        if len({t['inputPCM_SHA256'] for t in trials}) != 1:
            raise ValueError('Policies did not receive identical tagged inputs')
        for value in trials[1:]:
            value['gatesAgainstControl'] = gates(trials[0], value)
            value['failedGates'] = [name for name, okay in value['gatesAgainstControl'].items() if not okay]
        rows.append({'recipe': item['name'], 'recipePath': relative(path), 'recipeSHA256': sha(path), 'trials': trials})
    result = {'schemaVersion': 1, 'analyzerSHA256': sha(Path(__file__)), 'specSHA256': sha(args.spec),
              'recipes': rows, 'limits': [
                  'Tap observes generated PCM before the muted mixer, not actual speaker output.',
                  'An individual favorable trial is not phase-repeat qualification; all gates are necessary, not sufficient.',
                  'Initial and final intentional silence is excluded; complete first-to-last source IDs are required.',
                  'Recorded burst slices use synthetic warmup and do not reproduce original game/queue state or host flush.',
                  'Report/harness/source/input/output identities are pinned. No game engine or production changes.']}
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.out), 'sha256': sha(args.out), 'recipes': len(rows)}))


if __name__ == '__main__':
    main()
