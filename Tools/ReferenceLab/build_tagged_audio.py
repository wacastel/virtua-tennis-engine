#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a generated-PCM audio experiment from an explicitly selected source.

This never patches production source, links a game engine, or runs audio.
The output directory must be fresh. Historical trials are never overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio-source', required=True, type=Path,
                   help='Explicit AudioOutput.swift implementing VTAudioOutput, unchanged or candidate')
    p.add_argument('--expected-source-sha256', help='Optional required source identity')
    p.add_argument('--out', required=True, type=Path, help='Fresh ignored laboratory output directory')
    args = p.parse_args()
    audio = args.audio_source.resolve()
    harness = Path(__file__).with_name('TaggedAudioHarness.swift')
    before = {'audio': sha(audio), 'harness': sha(harness), 'builder': sha(Path(__file__))}
    if args.expected_source_sha256 and before['audio'] != args.expected_source_sha256:
        p.error('Audio source identity mismatch')
    if args.out.exists():
        p.error('Output already exists; choose a fresh directory to preserve previous evidence')
    args.out.mkdir(parents=True)
    copied = [args.out / 'AudioOutput.swift', args.out / 'TaggedAudioHarness.swift']
    for source, target in zip([audio, harness], copied):
        shutil.copyfile(source, target)
        assert sha(source) == sha(target)
    shutil.copyfile(Path(__file__), args.out / 'build_tagged_audio.py')
    executable = args.out / 'tagged-audio-harness'
    command = ['xcrun', 'swiftc', '-parse-as-library', '-O', '-target', 'arm64-apple-macos14.0',
               '-framework', 'AVFoundation', '-framework', 'CryptoKit',
               *map(str, copied), '-o', str(executable)]
    subprocess.run(command, check=True)
    if before != {'audio': sha(audio), 'harness': sha(harness), 'builder': sha(Path(__file__))}:
        raise RuntimeError('Input source changed during compilation')
    manifest = {'schemaVersion': 1, 'sourceSHA256': before,
                'copiedSources': {x.name: sha(x) for x in copied},
                'executable': executable.name, 'executableSHA256': sha(executable),
                'compiler': subprocess.check_output(['xcrun', 'swiftc', '--version'], text=True).strip(),
                'target': 'arm64-apple-macos14.0', 'frameworks': ['AVFoundation', 'CryptoKit'],
                'sourceTransformed': False, 'engineLinked': False,
                'scope': 'Generated tagged PCM through a muted real output graph; developer tool only. '
                         'This build command does not run audio or alter app/preferences.'}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'executable': str(executable), 'sha256': manifest['executableSHA256']}))


if __name__ == '__main__':
    main()
