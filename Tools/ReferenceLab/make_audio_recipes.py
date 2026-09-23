#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Freeze identical tagged-audio schedules from completed producer event logs.

No PCM/media is read. Outputs contain numeric arrival times and batch counts.
The first/continued burst recipes omit the host flush and use a fixed one-second
synthetic warmup; they do not reproduce original game state or queue history.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace', required=True, type=Path, help='Completed audio-batches.jsonl')
    p.add_argument('--single-miss-events', required=True, type=Path,
                   help='Completed synthetic starvation producer-events.json')
    p.add_argument('--out', required=True, type=Path, help='Fresh output directory')
    args = p.parse_args()
    if args.out.exists():
        p.error('Output exists; refuse to overwrite an earlier recipe')
    rows = [json.loads(line) for line in args.trace.read_text().splitlines() if line.strip()]
    miss = json.loads(args.single_miss_events.read_text())
    args.out.mkdir(parents=True)

    def write(name, events, source):
        if not events or any(b['arrivalSeconds'] < a['arrivalSeconds'] for a, b in zip(events, events[1:])):
            raise ValueError('Missing or out-of-order producer events')
        value = {'name': name, 'events': events,
                 'durationSeconds': events[-1]['arrivalSeconds'] + .6, 'source': source,
                 'generatorSHA256': sha(Path(__file__)),
                 'scope': 'Synthetic tagged PCM only. Identical scheduled inputs for each audio policy; '
                          'actual arrival lateness and complete source-tag conservation must be checked.'}
        (args.out / (name + '.json')).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

    events = [{'kind': 'batch', 'arrivalSeconds': x['arrivalSeconds'], 'batch': x['batch']}
              for x in miss if x['kind'] == 'enqueue']
    write('single-miss', events, {'producerEventsSHA256': sha(args.single_miss_events)})
    warmup = [{'kind': 'batch', 'arrivalSeconds': i * 738 / 44100, 'batch': 738} for i in range(60)]
    for name, low, high in [('first-burst', 4100, 4310), ('continue-burst', 9280, 9500)]:
        selected = [x for x in rows if x['kind'] == 'enqueueBefore' and low <= x['frame'] <= high]
        if [x['frame'] for x in selected] != list(range(low, high + 1)):
            raise ValueError(f'Incomplete source-frame interval {low}..{high}')
        start = selected[0]['enqueueArrivalSeconds']
        events = warmup + [{'kind': 'batch', 'arrivalSeconds': 1.05 + x['enqueueArrivalSeconds'] - start,
                           'batch': x['batchSampleFrames'], 'sourceFrame': x['frame']} for x in selected]
        last = events[-1]['arrivalSeconds']
        events.extend({'kind': 'batch', 'arrivalSeconds': last + (i + 1) * 738 / 44100,
                       'batch': 738} for i in range(30))
        write(name, events, {'traceSHA256': sha(args.trace), 'firstFrame': low, 'lastFrame': high,
                            'originalHostFlushIncluded': False, 'syntheticWarmupBatches': 60})


if __name__ == '__main__':
    main()
