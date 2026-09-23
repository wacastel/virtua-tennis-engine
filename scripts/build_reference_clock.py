#!/usr/bin/env python3
"""Build original CPUs with the SH4 interpreter's slowdown multiplier removed.

This derived reference changes one constant from eight to one. STRICT_MODE,
instruction semantics and all other hardware options remain unchanged. The
pristine original checkout and library are retained separately.
"""
from __future__ import annotations
import argparse,hashlib,json,subprocess,time
from pathlib import Path
from build_reference import ROOT,REVISION,MODULES,sha,capture,verify_tree,save

ORIGINAL=ROOT/'build/reference-source/flycast'
ORIGINAL_BUILD=ROOT/'build/reference'
SOURCE=ROOT/'build/clock-source/flycast'
BUILD=ROOT/'build/reference-clock'
HEADER='core/hw/sh4/sh4_interpreter.h'

def inventory():
    # Normalize upstream CMake's known zlib-header rename in the derived tree.
    subroots=[SOURCE/x for x in MODULES]+[SOURCE/'core/deps/tinygettext/external/tinycmmc']
    children={str(p.relative_to(SOURCE)):verify_tree(p) for p in subroots}
    if capture(['git','rev-parse','HEAD'],SOURCE)!=REVISION:
        raise RuntimeError('Unexpected derived-reference upstream revision')
    changed=capture(['git','diff','--name-only','HEAD'],SOURCE).splitlines()
    if changed!=[HEADER]:raise RuntimeError('Unexpected derived source change: '+str(changed))
    files={}
    for name in subprocess.check_output(['git','ls-files','-z'],cwd=SOURCE).decode().split('\0'):
        if name and (SOURCE/name).is_file():files[name]=sha(SOURCE/name)
    return {'.':{'commit':REVISION,'files':files},**children}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=int,default=6)
    args=parser.parse_args();start=time.time()
    original_manifest=json.loads((ORIGINAL_BUILD/'build-manifest.json').read_text())
    if sha(ORIGINAL_BUILD/'flycast_libretro.dylib')!=original_manifest['productSHA256']:
        raise RuntimeError('Original reference library identity changed')
    expected=json.loads((ROOT/'Configuration/sh4-source.json').read_text())['files'][HEADER]
    if sha(ORIGINAL/HEADER)!=expected:raise RuntimeError('Unexpected original interpreter header')
    original=(ORIGINAL/HEADER).read_text()
    anchor='static constexpr int CPU_RATIO = 8;'
    if original.count(anchor)!=1:raise RuntimeError('Clock patch anchor changed')
    replacement=original.replace(anchor,'static constexpr int CPU_RATIO = 1;',1)
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['cp','-cR',str(ORIGINAL),str(SOURCE)],check=True)
        (SOURCE/HEADER).write_text(replacement)
    elif (SOURCE/HEADER).read_text()!=replacement:
        raise RuntimeError('Existing derived reference has a different clock patch')
    BUILD.mkdir(parents=True,exist_ok=True)
    before=inventory();save(BUILD/'upstream-files.json',before)
    command=[arg.replace('PROJECT',str(ROOT)) for arg in original_manifest['configurationArguments']]
    command[command.index('-S')+1]=str(SOURCE)
    command[command.index('-B')+1]=str(BUILD)
    with (BUILD/'configure.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (BUILD/'build.log').open('w') as log:
        subprocess.run([command[0],'--build',str(BUILD),'-j',str(args.jobs)],stdout=log,stderr=subprocess.STDOUT,check=True)
    if inventory()!=before:raise RuntimeError('Derived source changed during build')
    if sha(ORIGINAL/HEADER)!=expected:raise RuntimeError('Pristine original header changed')
    product=BUILD/'flycast_libretro.dylib'
    save(BUILD/'build-manifest.json',{
        'purpose':'Original SH4/ARM7/AICA DSP reference with one documented SH4 clock correction',
        'upstreamCommit':REVISION,'originalReferenceSHA256':original_manifest['productSHA256'],
        'sourcePatches':[{'path':HEADER,'originalSHA256':expected,'derivedSHA256':sha(SOURCE/HEADER),
            'change':'Non-STRICT Sh4Interpreter::CPU_RATIO8→1; no other source changes'}],
        'sh4ClockHz':200000000,'interpreterCycleMultiplier':1,'strictMode':False,
        'referenceCPUConfiguration':'TARGET_NO_REC: all three original interpreter backends',
        'upstreamFileInventorySHA256':sha(BUILD/'upstream-files.json'),
        'product':str(product.relative_to(ROOT)),'productSHA256':sha(product),
        'configurationArguments':[s.replace(str(ROOT),'PROJECT') for s in command],
        'architectures':capture(['lipo','-archs',product]),'scriptSHA256':sha(__file__),
        'elapsedSeconds':round(time.time()-start,3)})
    print(json.dumps({'reference':str(product),'sha256':sha(product),'clockMultiplier':1}))

if __name__=='__main__':main()
