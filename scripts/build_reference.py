#!/usr/bin/env python3
"""Build the pinned original-CPU NAOMI laboratory, never the shipped game."""
from __future__ import annotations
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REVISION='628bd3dbb160ea2750230fc6b00c0bb8173cb1f6'
SOURCE=ROOT/'build/reference-source/flycast'
BUILD=ROOT/'build/reference'
MODULES=['core/deps/libchdr','core/deps/asio','core/deps/tinygettext']

def capture(args,cwd=None):
    return subprocess.check_output([str(x) for x in args],cwd=cwd,text=True).strip()
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
def verify_tree(path):
    # Upstream zlib's out-of-source CMake configuration renames this shipped
    # header and generates a platform-specific one in the build directory.
    # Restore only that byte-identical upstream rename before the source audit.
    if path.name=='libchdr':
        rel='deps/zlib-1.3.1/zconf.h'
        original=path/rel; renamed=path/(rel+'.included')
        if not original.exists() and renamed.is_file():
            expected=subprocess.check_output(['git','show','HEAD:'+rel],cwd=path)
            if renamed.read_bytes()!=expected:raise RuntimeError('Changed zlib source header')
            renamed.rename(original)
    if capture(['git','diff','--name-only','HEAD'],path):
        raise RuntimeError('Tracked upstream source modified: '+str(path))
    entries={}
    for name in subprocess.check_output(['git','ls-files','-z'],cwd=path).decode().split('\0'):
        if not name:continue
        p=path/name
        if p.is_file():entries[name]=sha(p)
    return {'commit':capture(['git','rev-parse','HEAD'],path),'files':entries}
def prepare():
    SOURCE.parent.mkdir(parents=True,exist_ok=True)
    if not (SOURCE/'.git').exists():
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout','https://github.com/flyinghead/flycast.git',str(SOURCE)],check=True)
        subprocess.run(['git','checkout','--detach',REVISION],cwd=SOURCE,check=True)
    if capture(['git','rev-parse','HEAD'],SOURCE)!=REVISION:
        raise RuntimeError('Unexpected upstream revision')
    subprocess.run(['git','submodule','update','--init','--recursive','--depth','1',*MODULES],cwd=SOURCE,check=True)
    roots=[SOURCE]+[SOURCE/x for x in MODULES]+[SOURCE/'core/deps/tinygettext/external/tinycmmc']
    trees={str(p.relative_to(SOURCE)) if p!=SOURCE else '.':verify_tree(p) for p in roots}
    save(BUILD/'upstream-files.json',trees)
    return trees

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jobs',type=int,default=6);p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args()
    if platform.system()!='Darwin' or platform.machine()!='arm64':p.error('Apple Silicon macOS required')
    if args.jobs<1:p.error('--jobs must be positive')
    start=time.time();BUILD.mkdir(parents=True,exist_ok=True);before=prepare()
    if args.prepare_only:return
    tooling=ROOT/'build/tooling'
    if not (tooling/'bin/cmake').is_file():
        subprocess.run([sys.executable,'-m','venv',str(tooling)],check=True)
        subprocess.run([str(tooling/'bin/python'),'-m','pip','install','--disable-pip-version-check','cmake==3.31.10','ninja==1.13.0'],check=True)
    cmake=tooling/'bin/cmake'
    command=[str(cmake),'-S',str(SOURCE),'-B',str(BUILD),'-G','Ninja',
        '-DCMAKE_MAKE_PROGRAM='+str(tooling/'bin/ninja'),'-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_OSX_ARCHITECTURES=arm64','-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0',
        '-DLIBRETRO=ON','-DUSE_OPENGL=ON','-DUSE_VULKAN=OFF','-DUSE_LUA=OFF',
        '-DUSE_BREAKPAD=OFF','-DUSE_HOST_LIBZIP=OFF','-DUSE_OPENMP=OFF','-DBUILD_TESTING=OFF',
        '-DCMAKE_CXX_FLAGS=-DTARGET_NO_REC']
    with (BUILD/'configure.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (BUILD/'build.log').open('w') as log:subprocess.run([str(cmake),'--build',str(BUILD),'-j',str(args.jobs)],stdout=log,stderr=subprocess.STDOUT,check=True)
    for rel,tree in before.items():
        if verify_tree(SOURCE/rel)!=tree:raise RuntimeError('Upstream changed during build: '+rel)
    product=BUILD/'flycast_libretro.dylib'
    arch=capture(['lipo','-archs',product]);assert arch=='arm64',arch
    save(BUILD/'build-manifest.json',{
        'purpose':'Original SH4/ARM7/AICA DSP development reference only',
        'upstreamCommit':REVISION,'sourcePatches':[],
        'upstreamFileInventorySHA256':sha(BUILD/'upstream-files.json'),
        'referenceCPUConfiguration':'TARGET_NO_REC: all three original interpreter backends',
        'cmakeVersion':capture([cmake,'--version']).splitlines()[0],
        'compiler':capture(['clang++','--version']),
        'configurationArguments':[str(x).replace(str(ROOT),'PROJECT') for x in command],
        'product':str(product.relative_to(ROOT)),'productSHA256':sha(product),
        'architectures':arch,'scriptSHA256':sha(__file__),
        'dependencies':capture(['otool','-L',product]).splitlines(),
        'elapsedSeconds':round(time.time()-start,3)})
    print(json.dumps({'reference':str(product),'sha256':sha(product),'architecture':arch}))
if __name__=='__main__':main()
