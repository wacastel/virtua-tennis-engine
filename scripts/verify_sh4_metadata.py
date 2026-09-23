#!/usr/bin/env python3
"""Compare all 65,536 offline SH4 classifications with original OpDesc tables.

The oracle is a separate development library linked to original reference
objects. No original executable, object or source is modified.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import compile_command, link_command, REFERENCE, SOURCE
from compile_sh4 import metadata, MEMORY_TYPES

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'build/verification/sh4-metadata')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    table=metadata(SOURCE)
    names=sorted({r['handler'] for r in table});name_ids={n:i for i,n in enumerate(names)}
    oracle=out/'oracle.cpp'
    oracle.write_text('''#include "hw/sh4/interpr/sh4_opcodes.h"
#include "reios/reios.h"
extern "C" void vt_sh4_metadata(unsigned word, unsigned *out) {
    auto *entry = OpDesc[word];
    out[0] = entry->IsFloatingPoint(); out[1] = entry->IssueCycles;
    out[2] = entry->unit; out[3] = entry->ex_type;
    out[4] = 0xffffffff;
'''+''.join(f'if (entry->oph == {n}) out[4] = {i};\n' for n,i in name_ids.items())+'}\n')
    obj=out/'oracle.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',oracle,obj)
    with (out/'compile.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    lib=out/'original_metadata.dylib';cmd=link_command({},lib)
    exported=(SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_sh4_metadata\n'
    export_file=out/'exports.txt';export_file.write_text(exported)
    cmd=[('-Wl,-exported_symbols_list,'+str(export_file)) if x.startswith('-Wl,-exported_symbols_list,') else x for x in cmd]
    cmd.append(str(obj))
    with (out/'link.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(lib));fn=dll.vt_sh4_metadata;fn.argtypes=[ctypes.c_uint,ctypes.POINTER(ctypes.c_uint)]
    units={n:i for i,n in enumerate(['MT','EX','BR','LS','FE','CO'])}
    buffer=(ctypes.c_uint*5)()
    for word,expected in enumerate(table):
        fn(word,buffer)
        actual=[bool(buffer[0]),buffer[1],buffer[2],buffer[3] in MEMORY_TYPES,buffer[4]]
        want=[expected['floating'],expected['issue'],units[expected['unit']],expected['memory'],name_ids[expected['handler']]]
        if actual!=want:raise RuntimeError(f'Original SH4 metadata mismatch at {word:04x}: {actual} != {want}')
    report={'passed':True,'instructionWords':65536,'comparisons':['original handler pointer identity','FPU disabled classification','issue cycles','execution unit','memory-cycle classification'],
        'referenceLibrarySHA256':sha(REFERENCE/'flycast_libretro.dylib'),'oracleLibrarySHA256':sha(lib),
        'oracleSourceSHA256':sha(oracle),'generatorSHA256':sha(ROOT/'scripts/compile_sh4.py'),
        'verifierSHA256':sha(__file__),'scope':'Exhaustive metadata mapping, not instruction execution or gameplay.'}
    (ROOT/'Documentation').mkdir(exist_ok=True)
    (ROOT/'Documentation/sh4-metadata-acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
