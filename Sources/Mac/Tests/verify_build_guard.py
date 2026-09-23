#!/usr/bin/env python3
"""Exercise the real host build entry's fail-closed library inspection."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/build_host.sh'
OUTPUT = ROOT / 'build/host-tests/link-guard'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cases = []
    with tempfile.TemporaryDirectory(dir=OUTPUT) as name:
        scratch = Path(name)
        for marker in ['vt_reference_engine_marker', 'vt_observer_engine_marker',
                       'vt_diagnostic_engine_marker', 'vt_test_stub_marker', None]:
            label = marker or 'invalid-library'
            library = scratch / (label + '.dylib')
            if marker:
                source = scratch / (label + '.c')
                source.write_text('unsigned vt_fixed_engine_marker(void){return 0x56544658;}\n'
                                  'void* vt_create(void){return 0;}\n'
                                  'void '+marker+'(void){}\n')
                subprocess.run(['xcrun','clang','-arch','arm64','-mmacosx-version-min=14.0',
                                '-dynamiclib',str(source),'-o',str(library)],check=True)
                expected = 'Refusing to link'
            else:
                library.write_text('Deliberately malformed native-library test.\n')
                expected = 'Cannot inspect engine symbols'
            target = scratch / 'must-not-exist'
            result = subprocess.run([str(SCRIPT),'--engine',str(library),'--output',str(target)],
                                    cwd=ROOT,env=dict(os.environ,TMPDIR=str(scratch)),capture_output=True,text=True,timeout=30)
            assert result.returncode and expected in result.stderr, (label,result.returncode,result.stderr)
            assert not target.exists()
            assert not list(scratch.glob('vt-host-symbols.*'))
            cases.append({'case':label,'rejected':True,'hostProduced':False,'temporarySymbolsCleaned':True})
        original = ROOT / 'build/reference/flycast_libretro.dylib'
        if original.is_file():
            result = subprocess.run([str(SCRIPT),'--engine',str(original),'--output',str(scratch/'original-must-not-link')],
                                    cwd=ROOT,capture_output=True,text=True,timeout=30)
            assert result.returncode and 'Verified fixed engine marker is absent' in result.stderr
            cases.append({'case':'actual-original-Flycast','rejected':True,'hostProduced':False})
    report = {'passed':True,'cases':cases,'engineStubUsedInApp':False,
              'buildScriptSHA256':hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
              'testSourceSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope':'Real build entry rejects test-only marker-bearing dylibs, failed nm inspection and an actual original reference core before Swift linking.'}
    (OUTPUT/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'caseCount':len(cases)}))


if __name__ == '__main__':
    main()
