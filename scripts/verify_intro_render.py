#!/usr/bin/env python3
"""Check the bounded intro RTT correction against completed original-core runs.

Hash changes establish which pictures changed, not whether they look correct.
The stadium detail and grayscale appearance require separate visual review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STEPS = 3600
GREY_STEPS = (1260, 1560, 1620, 1920, 1980, 2400, 2700, 2940, 3000, 3240, 3300)
COLOR_STEPS = (960, 1080, 1200, 1320, 1500, 1680, 1860, 2040, 2280, 2460,
               2640, 2820, 3120, 3480, 3600)
ROW_FIELDS = {'frame', 'buttons', 'width', 'height', 'duplicate', 'rgbaSHA256',
              'audioFrames', 'pcmSHA256', 'sh4Ticks', 'stepTicks'}
RTT_OPTION = 'reicast_enable_rttb'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def read_run(folder):
    report_path, frames_path = folder / 'report.json', folder / 'frames.jsonl'
    report = json.loads(report_path.read_text())
    rows = [json.loads(line) for line in frames_path.read_text().splitlines()]
    require(len(rows) == report['frames'] and len(rows) >= STEPS, 'Incomplete intro run')
    require(report['schedulerTicksAvailable'] is True, 'Scheduler evidence is required')
    ticks = 0
    for number, row in enumerate(rows, 1):
        require(set(row) == ROW_FIELDS, f'Unexpected frame fields at step {number}')
        require(row['frame'] == number, 'Discontinuous frame sequence')
        require(row['sh4Ticks'] > ticks and row['stepTicks'] == row['sh4Ticks'] - ticks,
                f'Inconsistent scheduler ticks at step {number}')
        require(isinstance(row['audioFrames'], int) and row['audioFrames'] >= 0,
                f'Invalid audio count at step {number}')
        for field in ('rgbaSHA256', 'pcmSHA256'):
            require(re.fullmatch('[0-9a-f]{64}', row[field]) is not None,
                    f'Invalid {field} at step {number}')
        ticks = row['sh4Ticks']
    require(sum(row['audioFrames'] for row in rows) == report['stereoSampleFrames'],
            'Inconsistent total audio count')
    require(rows[-1]['rgbaSHA256'] == report['rgbaSHA256'], 'Final picture differs from report')
    return report, rows, {'reportSHA256': sha(report_path), 'framesSHA256': sha(frames_path)}


def compare(before, after, expected_core):
    old, old_rows, old_pins = read_run(before)
    new, new_rows, new_pins = read_run(after)
    require(old['coreSHA256'] == new['coreSHA256'] == expected_core,
            'Runs must use the pinned corrected-clock original core')
    for key in ('contentSHA256', 'routeSHA256', 'sampleRate', 'width', 'height',
                'aspectRatio', 'fps', 'coreName', 'coreVersion', 'stepUnit',
                'glVendor', 'glRenderer', 'glVersion', 'bottomLeftOrigin'):
        require(old[key] == new[key], 'Different run inputs/configuration: ' + key)
    old_options, new_options = old['coreOptions'].copy(), new['coreOptions'].copy()
    require(old_options.pop(RTT_OPTION) == 'disabled' and new_options.pop(RTT_OPTION) == 'enabled',
            'Expected RTT readback disabled -> enabled')
    require(old_options == new_options, 'An unrelated core option changed')
    altered = []
    for number, (left, right) in enumerate(zip(old_rows[:STEPS], new_rows[:STEPS]), 1):
        require(all(left[key] == right[key] for key in ROW_FIELDS - {'rgbaSHA256'}),
                f'Non-picture output/input changed at step {number}')
        if left['rgbaSHA256'] != right['rgbaSHA256']:
            altered.append(number)
    require(all(step in altered for step in GREY_STEPS), 'A selected grey-scene picture did not change')
    require(not any(step in altered for step in COLOR_STEPS), 'A selected color-scene picture changed')
    if len(old_rows) == len(new_rows):
        require(old['pcmStreamSHA256'] == new['pcmStreamSHA256'], 'Complete PCM streams differ')
    evidence = []
    for folder, report, pins in ((before, old, old_pins), (after, new, new_pins)):
        samples = {}
        for step in (*GREY_STEPS, *COLOR_STEPS):
            path = folder / f'frame-{step}.png'
            require(path.is_file(), f'Missing visual-review capture: frame-{step}.png')
            samples[str(step)] = sha(path)
        evidence.append({**pins, 'coreSHA256': report['coreSHA256'],
                         'contentSHA256': report['contentSHA256'],
                         'routeSHA256': report['routeSHA256'],
                         'harnessSHA256': report['harnessSHA256'],
                         'totalRunSteps': report['frames'], 'captureSHA256': samples,
                         'RTTReadback': report['coreOptions'][RTT_OPTION]})
    # Re-read reports/traces so a concurrent unfinished run cannot be accepted.
    for folder, pins in ((before, old_pins), (after, new_pins)):
        require(sha(folder / 'report.json') == pins['reportSHA256'] and
                sha(folder / 'frames.jsonl') == pins['framesSHA256'], 'Run changed during verification')
    return {'passed': True, 'comparedSteps': STEPS, 'changedPictureSteps': altered,
            'changedPictureCount': len(altered), 'greySamplesChanged': list(GREY_STEPS),
            'colorSamplesUnchanged': list(COLOR_STEPS),
            'exactPCM_Counts_Input_Clock_Dimensions_DuplicateFlags': True,
            'stereoSampleFrames': sum(row['audioFrames'] for row in old_rows[:STEPS]),
            'emulatedSeconds': old_rows[STEPS - 1]['sh4Ticks'] / 200000000,
            'identicalHarnessBinary': old['harnessSHA256'] == new['harnessSHA256'],
            'runs': evidence,
            'scope': 'The first 3,600 original-core presentation steps with RTT readback as the sole core option change. Picture hashes change at selected grey scenes and remain identical at selected color scenes; all PCM, counts, input, dimensions, duplicate flags and scheduler ticks match. Captures are hashed for separate manual visual inspection. This script does not assess image quality, native CPU parity, real-time performance or physical arcade-board equivalence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reference = ROOT / 'build/reference-clock/build-manifest.json'
    manifest = json.loads(reference.read_text())
    require(manifest.get('interpreterCycleMultiplier') == 1 and manifest.get('sh4ClockHz') == 200000000,
            'The corrected 200 MHz original reference is required')
    require(sha(ROOT / manifest['product']) == manifest['productSHA256'], 'Original reference changed')
    probe_path = ROOT / 'build/reference-clock-probe/manifest.json'
    probe = json.loads(probe_path.read_text())
    require(probe.get('referenceSHA256') == manifest['productSHA256'] and
            probe.get('observer') is False and probe.get('originalCPUOperationsChanged') is False and
            probe.get('shippingMarkerPresent') is False, 'Expected the read-only original-core clock probe')
    require(sha(ROOT / probe['source']) == probe['probeSourceSHA256'] and
            sha(ROOT / probe['product']) == probe['productSHA256'], 'Clock-probe inputs changed')
    result = compare(args.before, args.after, probe['productSHA256'])
    result.update(scriptSHA256=sha(__file__), referenceManifestSHA256=sha(reference),
                  clockProbeManifestSHA256=sha(probe_path))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: result[key] for key in ('passed', 'comparedSteps', 'changedPictureCount')}))


if __name__ == '__main__':
    main()
