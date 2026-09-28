#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a ROM-free synthetic test of the unchanged production texture helper.

Never starts the harness. Run its executable with a fresh output directory.
Optional --benchmark-iterations is a harness argument; coordinate timing with
other performance jobs. No game engine, audio device or media is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True,
                        help='Fresh directory inside ignored build/')
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / 'build') or out.exists():
        parser.error('Output must be a new directory inside ignored build/')
    helper = ROOT / 'Sources/Mac/FramePresentation.swift'
    harness = Path(__file__).with_name('TexturePresentationHarness.swift')
    witnesses = [helper, harness, Path(__file__), ROOT / 'Sources/Mac/NativeGame.swift']
    identities = {str(p.relative_to(ROOT)): sha(p) for p in witnesses}
    out.mkdir(parents=True)
    copied = []
    for source in (helper, harness):
        target = out / source.name
        shutil.copyfile(source, target)
        if sha(source) != sha(target):
            raise RuntimeError('Copied source differs from production/source input')
        copied.append(target)
    executable = out / 'texture-presentation-harness'
    command = ['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library', '-O',
               '-target', 'arm64-apple-macos14.0', '-framework', 'AppKit',
               '-framework', 'SpriteKit', '-framework', 'CryptoKit',
               '-framework', 'ImageIO', '-framework', 'UniformTypeIdentifiers',
               *map(str, copied), '-o', str(executable)]
    subprocess.run(command, check=True)
    if identities != {str(p.relative_to(ROOT)): sha(p) for p in witnesses}:
        raise RuntimeError('Input source changed during compilation')
    manifest = {'schemaVersion': 1, 'sources': identities,
                'copiedSources': {p.name: sha(p) for p in copied},
                'executable': executable.name, 'executableSHA256': sha(executable),
                'compiler': subprocess.check_output(['xcrun', 'swiftc', '--version'], text=True).strip(),
                'compileCommand': command, 'sourceTransformed': False,
                'engineLinked': False, 'productionHelper': str(helper.relative_to(ROOT)),
                'scope': 'Actual production helper plus synthetic comparison against the prior '
                         'DeviceRGB/noneSkipLast CGImage path. Build only; no test or engine run.'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'executable': str(executable), 'executableSHA256': sha(executable), 'ran': False}))


if __name__ == '__main__':
    main()
