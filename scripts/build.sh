#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    cat <<'EOF'
Usage: scripts/build.sh [--media-root DIRECTORY] [--jobs N] [--skip-engine]
Build build/Virtua Tennis.app from verified local media and fixed CPU sources.
--media-root DIRECTORY  Import the supplied cartridge/BIOS files from this tree.
--jobs N                Parallel compiler jobs for prepare_native.py (default 6).
--skip-engine           Package an existing, source-matched shipping engine.
EOF
}
JOBS=6
MEDIA_ROOT=
SKIP_ENGINE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --media-root) [[ $# -ge 2 && "$2" != --* ]] || { usage >&2; exit 2; }; MEDIA_ROOT="$2"; shift 2 ;;
        --jobs) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; JOBS="$2"; shift 2 ;;
        --skip-engine) SKIP_ENGINE=1; shift ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo '--jobs must be a positive integer.' >&2; exit 2; }
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Apple Silicon macOS is required.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3.11 or later is required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or later is required"'
xcrun --find swiftc >/dev/null
xcrun --find actool >/dev/null

if [[ -n "$MEDIA_ROOT" ]]; then
    python3 scripts/import_assets.py --media-root "$MEDIA_ROOT"
elif [[ ! -f build/assets/manifest.json ]]; then
    python3 scripts/import_assets.py
fi
if [[ "$SKIP_ENGINE" == 0 ]]; then
    [[ -f scripts/prepare_native.py ]] || { echo 'The fixed-engine preparation workflow is not available yet; no app was built.' >&2; exit 1; }
    python3 scripts/prepare_native.py --jobs "$JOBS"
fi

mkdir -p build
STAGING="$(mktemp -d "$PWD/build/package-work.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
APP="$STAGING/Virtua Tennis.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources"

# Validate source/object provenance before copying anything into an app.
python3 - "$STAGING" <<'PY'
from pathlib import Path
import hashlib,json,plistlib,re,shutil,subprocess,sys,zipfile
root=Path.cwd(); stage=Path(sys.argv[1]); app=stage/'Virtua Tennis.app'
sys.path.insert(0,str(root/'scripts'))
from native_provenance import (Snapshot, audit_cpu_inputs, match_engine_audit,
                               verify_snapshot, link_input_files, minimum_macos)
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def local(p):
    p=p.replace('PROJECT/',str(root)+'/'); value=Path(p)
    return value if value.is_absolute() else root/value
def check(p,digest):
    if not Path(p).is_file() or sha(p)!=digest:raise RuntimeError('Missing or changed build input: '+str(p))
engine=root/'build/native/libvirtua_tennis.dylib'; manifest_path=root/'build/native/manifest.json'
snapshot=Snapshot(root); m=snapshot.read_json(manifest_path)
for p in [root/'scripts/build.sh',root/'scripts/build_host.sh',root/'scripts/build_icon.sh',*sorted((root/'Sources/Mac').rglob('*.swift'))]:snapshot.add(p)
if m.get('shippingFixedEngine') is not True or m.get('diagnostic') is not False or m.get('runtimeInterpreterFallback') is not False:
    raise RuntimeError('Only a complete shipping fixed engine may be packaged')
if set(m.get('selectedCPUs',[]))!={'sh4','arm7','aicadsp'} or m.get('originalCPUsRemaining')!=[]:
    raise RuntimeError('All three original CPU execution backends must be replaced')
check(engine,m['productSHA256']); check(root/'scripts/build_engine.py',m['scriptSHA256'])
check(root/'scripts/native_provenance.py',m['provenanceHelperSHA256'])
snapshot.add(engine,m['productSHA256'])
snapshot.add(root/'scripts/native_provenance.py',m['provenanceHelperSHA256'])
snapshot.add_map(m['buildInputFiles'])
check(root/'Sources/Bridge/vt_fixed_fault.cpp',m['commonSourceSHA256'])
check(root/'Sources/Bridge/vt_fixed_fault.h',m['commonHeaderSHA256'])
clock_path=root/'build/reference-clock/build-manifest.json'
check(clock_path,m['referenceManifestSHA256']); clock=json.loads(clock_path.read_text())
if clock.get('sh4ClockHz')!=200000000 or clock.get('interpreterCycleMultiplier')!=1:
    raise RuntimeError('The engine must use the corrected 200 MHz reference build')
check(root/'build/reference-clock/flycast_libretro.dylib',m['referenceSHA256'])
for p,d in m['bridgeSources'].items():check(local(p),d)
if set(m['CPUManifests'])!={'sh4','arm7','aicadsp'}:raise RuntimeError('Incomplete CPU manifest set')
audit=audit_cpu_inputs(root,m['selectedCPUs'],m['CPUManifests'])
match_engine_audit(root,m,audit)
linked_inputs=link_input_files(root,m['linkCommand'],root/'build/reference-clock')
if linked_inputs!=m['linkedInputFiles']:raise RuntimeError('Engine link input inventory changed')
snapshot.add_map(audit['inputFiles']); snapshot.add_map(linked_inputs)
if minimum_macos(engine)!=m['minimumMacOS']:raise RuntimeError('Engine deployment record differs')
symbols=subprocess.check_output(['/usr/bin/nm','-C',str(engine)],text=True)
for banned in m['forbiddenSymbolsAbsent']:
    if banned in symbols:raise RuntimeError('Original CPU decoder remains: '+banned)
assets=root/'build/assets'; media=json.loads((assets/'manifest.json').read_text())
spec=json.loads((root/'Configuration/media.json').read_text())
check(root/'Configuration/media.json',media['mediaSpecificationSHA256'])
entries=[('vtennis.zip',media['archiveSHA256'],{r['name'] for r in spec['files']}),
         ('system/dc/naomi.zip',media['biosArchiveSHA256'],{spec['bios']})]
header=(root/'Sources/Bridge/media_identity.h').read_text(); packaged_media={}
for rel,digest,names in entries:
    p=assets/rel; check(p,digest)
    declaration='{'+json.dumps(rel)+', '+str(p.stat().st_size)+'ULL, '+json.dumps(digest)+'}'
    if header.count(declaration)!=1:raise RuntimeError('Compiled media identity differs: '+rel)
    with zipfile.ZipFile(p) as z:
        if len(z.infolist())!=len(names) or set(z.namelist())!=names:raise RuntimeError('Unexpected archive members: '+rel)
        for row in spec['files']:
            if row['name'] in names:
                data=z.read(row['name'])
                if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise RuntimeError('Media member differs: '+row['name'])
    target=app/'Contents/Resources/Assets'/rel; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
    check(target,digest); packaged_media[rel]={'bytes':p.stat().st_size,'sha256':digest}
notices=json.loads((root/'Documentation/third-party-notices.json').read_text())
for row in notices['licenseFiles']:check(root/row['preservedPath'],row['sha256'])
check(root/'Licenses/Source-Notices.txt',notices['sourceNoticeCollectionSHA256'])
shutil.copytree(root/'Licenses',app/'Contents/Resources/Licenses')
shutil.copy2(root/'COPYING',app/'Contents/Resources/COPYING')
shutil.copy2(root/'README.md',app/'Contents/Resources/README.md')
shutil.copy2(root/'Documentation/controls.md',app/'Contents/Resources/Controls.md')
shutil.copy2(engine,app/'Contents/Frameworks/libvirtua_tennis.dylib')
check(app/'Contents/Frameworks/libvirtua_tennis.dylib',m['productSHA256'])
info={'CFBundleName':'Virtua Tennis','CFBundleDisplayName':'Virtua Tennis','CFBundleExecutable':'VirtuaTennis',
      'CFBundleIdentifier':'local.william.virtuatennis','CFBundlePackageType':'APPL','CFBundleInfoDictionaryVersion':'6.0',
      'CFBundleShortVersionString':'1.0','CFBundleVersion':'1','LSMinimumSystemVersion':'14.0',
      'LSApplicationCategoryType':'public.app-category.sports-games','NSHighResolutionCapable':True,
      'CFBundleIconFile':'AppIcon','CFBundleIconName':'AppIcon','NSSupportsAutomaticGraphicsSwitching':True,
      'NSHumanReadableCopyright':'Original game © SEGA. Engine and component notices are included in Resources/Licenses.'}
with (app/'Contents/Info.plist').open('wb') as f:plistlib.dump(info,f,sort_keys=True)
report={'app':'build/Virtua Tennis.app','engineInputSHA256':m['productSHA256'],
        'engineManifestSHA256':sha(manifest_path),'CPUManifests':m['CPUManifests'],
        'bridgeSources':m['bridgeSources'],'media':packaged_media,
        'referenceClockHz':clock['sh4ClockHz'],'runtimeInterpreterFallback':False,
        'provenanceHelperSHA256':m['provenanceHelperSHA256'],
        'validatedBuildInputFiles':snapshot.pins,
        'sourceOnlyQualification':False,'gameplayOrAudioQualifiedByThisBuild':False}
verify_snapshot(root,snapshot.pins)
(stage/'manifest.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
PY

scripts/build_icon.sh
cp build/icon/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp build/icon/compiled/Assets.car "$APP/Contents/Resources/Assets.car"
scripts/build_host.sh --engine "$APP/Contents/Frameworks/libvirtua_tennis.dylib" --output "$APP/Contents/MacOS/VirtuaTennis"
/usr/bin/codesign --force --sign - "$APP/Contents/Frameworks/libvirtua_tennis.dylib"
/usr/bin/codesign --force --sign - "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"

python3 - "$STAGING" <<'PY'
from pathlib import Path
import hashlib,json,os,re,subprocess,sys
root=Path.cwd(); stage=Path(sys.argv[1]); app=stage/'Virtua Tennis.app'; report=json.loads((stage/'manifest.json').read_text())
sys.path.insert(0,str(root/'scripts'))
from native_provenance import verify_snapshot, minimum_macos
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
artifacts={}
for rel in ('Contents/MacOS/VirtuaTennis','Contents/Frameworks/libvirtua_tennis.dylib'):
    p=app/rel
    arch=subprocess.check_output(['/usr/bin/lipo','-archs',str(p)],text=True).strip()
    if arch!='arm64':raise RuntimeError('Unexpected architecture: '+arch)
    minimum=minimum_macos(p)
    deps=subprocess.check_output(['/usr/bin/otool','-L',str(p)],text=True).splitlines()[1:]
    for dep in deps:
        name=dep.strip().split(' (')[0]
        if not (name=='@rpath/libvirtua_tennis.dylib' or name.startswith(('/usr/lib/','/System/Library/Frameworks/'))):
            raise RuntimeError('External runtime dependency: '+name)
    artifacts[rel]={'sha256':sha(p),'architecture':arch,'minimumMacOS':minimum,'dependencies':[d.strip() for d in deps]}
if sha(root/'build/native/libvirtua_tennis.dylib')!=report['engineInputSHA256']:raise RuntimeError('Engine changed while packaging')
verify_snapshot(root,report['validatedBuildInputFiles'])
report.update(passed=True,artifacts=artifacts,signature='ad-hoc; deep/strict verification passed',
    iconSHA256=sha(app/'Contents/Resources/AppIcon.icns'),
    sourceSHA256={str(p.relative_to(root)):sha(p) for p in [root/'scripts/build.sh',root/'scripts/build_host.sh',root/'scripts/build_icon.sh',root/'scripts/native_provenance.py',*sorted((root/'Sources/Mac').rglob('*.swift'))]})
# Replace only this project's generated app, and never an app still executing.
target=root/'build/Virtua Tennis.app'; executable=str(target/'Contents/MacOS/VirtuaTennis')
processes=subprocess.check_output(['/bin/ps','-axo','args='],text=True).splitlines()
if any(line==executable or line.startswith(executable+' ') for line in processes):raise RuntimeError('Quit the existing Virtua Tennis app before replacing it')
previous=stage/'previous.app'
if target.exists():target.rename(previous)
try:app.rename(target)
except BaseException:
    if previous.exists():previous.rename(target)
    raise
out=root/'build/package';out.mkdir(exist_ok=True)
tmp=out/'manifest.json.partial';tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');os.replace(tmp,out/'manifest.json')
print(json.dumps({'app':report['app'],'executableSHA256':artifacts['Contents/MacOS/VirtuaTennis']['sha256'],
    'packageAuditPassed':True,'gameplayOrAudioQualifiedByThisBuild':False}))
PY
