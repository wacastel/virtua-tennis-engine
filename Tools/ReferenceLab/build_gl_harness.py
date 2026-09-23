#!/usr/bin/env python3
"""Build the original-engine-only offscreen OpenGL laboratory harness."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT / 'build/reference-source/flycast')
    p.add_argument('--output', type=Path, default=ROOT / 'build/reference-lab/gl-harness')
    a = p.parse_args()
    source = a.source.resolve()
    header = source / 'core/deps/libretro-common/include/libretro.h'
    if not header.is_file():
        p.error(f'Missing pinned libretro header: {header}')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    implementation = Path(__file__).with_name('GLHarness.mm')
    command = ['xcrun', 'clang++', '-std=c++17', '-O2', '-arch', 'arm64',
               '-mmacosx-version-min=14.0', '-fobjc-arc', '-DGL_SILENCE_DEPRECATION',
               '-Wno-deprecated-declarations', '-I', str(header.parent), str(implementation),
               '-framework', 'Foundation', '-framework', 'OpenGL', '-framework', 'CoreGraphics',
               '-framework', 'ImageIO', '-o', str(a.output)]
    subprocess.run(command, check=True)
    subprocess.run([str(a.output), '--self-test'], check=True)
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'referenceOnly': True, 'harnessSHA256': sha(implementation),
              'libretroHeaderSHA256': sha(header), 'executableSHA256': sha(a.output),
              'selfTest': 'CGL/readback and 16 independent player/coin routing checks passed',
              'deploymentTarget': '14.0', 'architecture': 'arm64'}
    a.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(a.output)


if __name__ == '__main__':
    main()
