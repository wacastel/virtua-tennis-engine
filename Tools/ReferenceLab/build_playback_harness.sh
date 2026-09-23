#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=build/playback-harness
ENGINE=build/native/libvirtua_tennis.dylib
[[ $# == 0 ]] || { echo 'Usage: Tools/ReferenceLab/build_playback_harness.sh' >&2; exit 2; }
[[ -f "$ENGINE" ]] || { echo 'Build the shipping fixed engine first.' >&2; exit 1; }
mkdir -p "$OUT"
python3 - <<'PY'
import hashlib,json,pathlib,subprocess
root=pathlib.Path.cwd(); engine=root/'build/native/libvirtua_tennis.dylib'
manifest=json.loads((root/'build/native/manifest.json').read_text())
assert manifest['shippingFixedEngine'] is True and manifest['diagnostic'] is False
assert manifest['runtimeInterpreterFallback'] is False and manifest['originalCPUsRemaining']==[]
assert set(manifest['selectedCPUs'])=={'sh4','arm7','aicadsp'}
assert hashlib.sha256(engine.read_bytes()).hexdigest()==manifest['productSHA256']
symbols=subprocess.check_output(['/usr/bin/nm','-gU',str(engine)],text=True)
assert '_vt_fixed_engine_marker' in symbols and '_vt_create' in symbols
assert not any(x in symbols for x in ('_vt_reference_marker','_vt_observer_marker','_vt_diagnostic_marker','_vt_test_stub_marker'))
PY
cp "$ENGINE" "$OUT/libvirtua_tennis.dylib"
SDK="$(xcrun --sdk macosx --show-sdk-path)"
xcrun swiftc -swift-version 5 -target arm64-apple-macosx14.0 -sdk "$SDK" -O \
    -framework AppKit -framework SpriteKit -framework AVFoundation -framework GameController -framework CoreGraphics \
    Sources/Mac/AudioOutput.swift Sources/Mac/Controls.swift Sources/Mac/FrameClock.swift \
    Sources/Mac/GameScene.swift Sources/Mac/FrameContinuation.swift Sources/Mac/FramePresentation.swift \
    Sources/Mac/Input.swift Sources/Mac/Media.swift Sources/Mac/NativeGame.swift \
    Tools/ReferenceLab/PlaybackHarness.swift "$OUT/libvirtua_tennis.dylib" \
    -Xlinker -rpath -Xlinker '@executable_path' -Xlinker -dead_strip -o "$OUT/playback-harness"
python3 - <<'PY'
import hashlib,json,pathlib,subprocess
root=pathlib.Path.cwd(); out=root/'build/playback-harness'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
sources=[*sorted((root/'Sources/Mac').glob('*.swift')),
         root/'Tools/ReferenceLab/PlaybackHarness.swift',root/'Tools/ReferenceLab/build_playback_harness.sh']
sources=[p for p in sources if p.name!='main.swift']
assert sha(out/'libvirtua_tennis.dylib')==sha(root/'build/native/libvirtua_tennis.dylib')
result={'developmentOnly':True,'visibleWindow':False,'appSourcesModified':False,
        'target':'arm64-apple-macosx14.0',
        'compiler':subprocess.check_output(['xcrun','swiftc','--version'],text=True).strip(),
        'sources':{str(p.relative_to(root)):sha(p) for p in sources},
        'executableSHA256':sha(out/'playback-harness'),'engineSHA256':sha(out/'libvirtua_tennis.dylib'),
        'engineManifestSHA256':sha(root/'build/native/manifest.json')}
(out/'manifest.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({'built':str(out.relative_to(root)/'playback-harness'),'engineSHA256':result['engineSHA256'],'ran':False}))
PY
