#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=link
ENGINE=build/native/libvirtua_tennis.dylib
OUTPUT=build/host/VirtuaTennis
while [[ $# -gt 0 ]]; do
    case "$1" in
        --typecheck) MODE=typecheck; shift ;;
        --self-test) MODE=selftest; shift ;;
        --engine) [[ $# -ge 2 ]] || exit 2; ENGINE="$2"; shift 2 ;;
        --output) [[ $# -ge 2 ]] || exit 2; OUTPUT="$2"; shift 2 ;;
        *) echo 'Usage: scripts/build_host.sh [--typecheck | --self-test] [--engine DYLIB] [--output EXECUTABLE]' >&2; exit 2 ;;
    esac
done
SDK="$(xcrun --sdk macosx --show-sdk-path)"
FLAGS=(-swift-version 5 -target arm64-apple-macosx14.0 -sdk "$SDK")
mkdir -p build/host-tests
if [[ "$MODE" == typecheck ]]; then
    xcrun swiftc "${FLAGS[@]}" -typecheck Sources/Mac/*.swift
elif [[ "$MODE" == selftest ]]; then
    xcrun swiftc "${FLAGS[@]}" -O -framework GameController \
        Sources/Mac/Input.swift Sources/Mac/Controls.swift Sources/Mac/FrameClock.swift \
        Sources/Mac/FrameContinuation.swift Sources/Mac/FramePresentation.swift \
        Sources/Mac/Tests/*.swift -o build/host-tests/input-tests
    build/host-tests/input-tests > build/host-tests/input-tests.json
else
    [[ -f "$ENGINE" ]] || { echo 'The verified fixed Virtua Tennis engine is not built.' >&2; exit 1; }
    SYMBOLS="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/vt-host-symbols.XXXXXX")"
    trap 'rm -f "$SYMBOLS"' EXIT
    /usr/bin/nm -gU "$ENGINE" > "$SYMBOLS" || { echo 'Cannot inspect engine symbols; refusing to link.' >&2; exit 1; }
    if /usr/bin/grep -E 'vt_(reference|observer|diagnostic|test_stub).*marker|vt_.*(reference|observer|diagnostic|test_stub).*marker' "$SYMBOLS" >/dev/null; then
        echo 'Refusing to link a reference, observer, diagnostic or stub engine.' >&2; exit 1
    else
        STATUS=$?
        [[ "$STATUS" == 1 ]] || { echo 'Engine marker inspection failed.' >&2; exit 1; }
    fi
    /usr/bin/grep -E '[[:space:]]_vt_fixed_engine_marker$' "$SYMBOLS" >/dev/null || { echo 'Verified fixed engine marker is absent.' >&2; exit 1; }
    /usr/bin/grep -E '[[:space:]]_vt_create$' "$SYMBOLS" >/dev/null || { echo 'Production C ABI is absent.' >&2; exit 1; }
    mkdir -p "$(dirname "$OUTPUT")"
    xcrun swiftc "${FLAGS[@]}" -O -framework AppKit -framework SpriteKit \
        -framework AVFoundation -framework GameController -framework CoreGraphics \
        Sources/Mac/*.swift "$ENGINE" -Xlinker -rpath -Xlinker '@executable_path/../Frameworks' \
        -Xlinker -dead_strip -o "$OUTPUT"
    printf 'Linked %s. Packaging must include the exact audited engine dylib.\n' "$OUTPUT"
fi
if [[ "$MODE" != link ]]; then
    HOST_TEST_MODE="$MODE" python3 - <<'PY'
from pathlib import Path
import hashlib,json,os,subprocess
mode=os.environ['HOST_TEST_MODE']
path=Path('build/host-tests')/('input-tests.json' if mode=='selftest' else 'typecheck.json')
result=json.loads(path.read_text()) if mode=='selftest' else {'passed':True,'engineLinked':False,'appProduced':False}
files=sorted(Path('Sources/Mac').rglob('*.swift'))+[Path('scripts/build_host.sh')]
result.update(mode=mode,sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              target='arm64-apple-macosx14.0',compiler=subprocess.check_output(['xcrun','swiftc','--version'],text=True).strip())
if mode=='selftest':result['executableSHA256']=hashlib.sha256(Path('build/host-tests/input-tests').read_bytes()).hexdigest()
path.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'passed':True,'mode':mode,'checkCount':result.get('checkCount'),'report':str(path),'engineLinked':False}))
PY
fi
