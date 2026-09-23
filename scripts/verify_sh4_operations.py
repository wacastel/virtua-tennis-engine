#!/usr/bin/env python3
"""Compare fixed SH4 register operations with original execution in a lab dylib.

These fixtures exercise context-local integer/FPU operations and original
pairing/cycle behavior. Bus, IRQ and delay-slot behavior is covered separately
by complete original/native replay, not claimed by these local fixtures.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, json, re, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import compile_command,link_command,REFERENCE,SOURCE
from compile_sh4 import metadata,brace_end

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'build/verification/sh4-operations')
    p.add_argument('--generated',type=Path,default=ROOT/'build/generated/sh4')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);gen=a.generated.resolve()
    generation=json.loads((gen/'manifest.json').read_text())
    body_by_name={}
    for rel in ('sh4_opcodes.cpp','sh4_fpu.cpp'):
        text=(SOURCE/'core/hw/sh4/interpr'/rel).read_text()
        for match in re.finditer(r'sh4op\((\w+)\)',text):
            b=text.index('{',match.end());body_by_name[match[1]]=text[b:brace_end(text,b)]
    # Explicitly reject external state, memory, control transfers and host-FPU
    # control mutations. Every selected operation acts only on Sh4Context.
    rejected=re.compile(r'ReadMem|WriteMem|ReadMemBO|WriteMemBO|ctx->pc|executeDelaySlot|Update|Exception|debugger|UTLB|CCN_|ocache|icache|iNimp|restoreHost|rounding|sq_buffer|doSqWrite|sh4_sched|CpuRunning')
    eligible={name for name,body in body_by_name.items() if not rejected.search(body)}
    table=metadata(SOURCE)
    # Recover the generated map's operation IDs from their pinned, constant
    # instantiations, not from any runtime opcode decoder.
    ids={}
    for path in gen.glob('vt_operations_*.cpp'):
        for match in re.finditer(r'void vt_operation_(\d+)\(.*?\n(.*?)\n}',path.read_text(),re.S):
            word=re.search(r'::\w+<0x([0-9a-f]+)>\(ctx\);',match[2])
            if word:ids[int(word[1],16)]=int(match[1])
    words=sorted(w for w in ids if table[w]['handler'] in eligible)
    if len(words)<10000:raise RuntimeError('Unexpectedly small pure-operation corpus')
    common=r'''
#include "hw/sh4/sh4_if.h"
#include "hw/sh4/sh4_core.h"
#include "hw/sh4/sh4_cycles.h"
#include <cstring>
#include <cstdint>
static void prepare(Sh4Context &ctx, unsigned seed) {
    std::memset(&ctx, 0, sizeof(ctx));
    uint32_t state = seed * 0x9e3779b9u + 0x1234567u;
    auto next = [&]() { state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state; };
    for (unsigned n=0;n<16;n++) {
        ctx.r[n]=next(); ctx.fr[n]=float(int(next()%200001)-100000)/127.0f;
        ctx.xf[n]=float(int(next()%200001)-100000)/251.0f;
    }
    for (unsigned n=0;n<8;n++) ctx.r_bank[n]=next();
    ctx.mac.full=(uint64_t(next())<<32)|next();
    ctx.gbr=next();ctx.ssr=next();ctx.spc=next();ctx.sgr=next();ctx.dbr=next();ctx.vbr=next();ctx.pr=next();ctx.fpul=next();
    ctx.pc=0x0c024002;
    ctx.sr.setFull(next() & ~0x8000u);
    ctx.fpscr.full=((seed&1)?0x80000:0)|((seed&2)?0x100000:0);
    if (seed==7) ctx.sr.FD=1;
    ctx.cycle_counter=100000;
}
'''
    original=out/'original.cpp'
    original.write_text(common+r'''
#include "hw/sh4/sh4_opcode_list.h"
extern "C" unsigned vt_original_case(unsigned word,unsigned seed,void *out,unsigned *exception) {
    Sh4Context ctx; prepare(ctx,seed); Sh4Cycles cycles(1);cycles.init(&ctx);
    exception[0]=exception[1]=0;
    try {
        for (int step=0;step<2;step++) {
            if (ctx.sr.FD==1 && OpDesc[word]->IsFloatingPoint()) throw SH4ThrownException(ctx.pc-2,Sh4Ex_FpuDisabled);
            OpPtr[word](&ctx,word);cycles.executeCycles(word);
        }
    } catch (const SH4ThrownException &e) {exception[0]=e.epc;exception[1]=e.expEvn;}
    std::memcpy(out,&ctx,sizeof(ctx));return sizeof(ctx);
}
''')
    fixed=out/'fixed.cpp'
    fixed.write_text(common+'\n#include "vt_sh4_fixed.h"\n'+
        'static VTFixedOperation functions[] = {'+','.join('&vt_operation_'+str(ids[w]) for w in words)+'};\n'+r'''
extern "C" unsigned vt_fixed_case(unsigned index,unsigned seed,void *out,unsigned *exception,unsigned corrupt) {
    Sh4Context ctx; prepare(ctx,seed); Sh4Cycles cycles(1);cycles.init(&ctx);
    exception[0]=exception[1]=0;
    try {for(int step=0;step<2;step++) functions[index](&ctx,cycles);}
    catch (const SH4ThrownException &e) {exception[0]=e.epc;exception[1]=e.expEvn;}
    if (corrupt) ctx.r[0]^=1;
    std::memcpy(out,&ctx,sizeof(ctx));return sizeof(ctx);
}
extern "C" const char *vt_fixed_error();
extern "C" void vt_fixed_clear_error();
extern "C" int vt_guard_case(unsigned pc,unsigned word) {
    vt_fixed_clear_error();
    Sh4Context ctx;prepare(ctx,0);ctx.pc=pc+2;
    Sh4Cycles cycles(1);cycles.init(&ctx);
    try {vt_sh4_execute(&ctx,word,cycles);}
    catch(const FlycastException&) {return std::strlen(vt_fixed_error()) > 0 ? 1 : -1;}
    return 0;
}
''')
    objects=[]
    for path in (original,fixed):
        obj=path.with_suffix('.o');cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',path,obj)
        if path==fixed:cmd[1:1]=['-I'+str(gen/'overlay/core'),'-I'+str(gen)]
        with path.with_suffix('.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
        objects.append(obj)
    manifest_path=ROOT/'build/native/cpu/sh4/manifest.json';manifest=json.loads(manifest_path.read_text())
    for rel,digest in manifest['objects'].items():
        if sha(ROOT/rel)!=digest:raise RuntimeError('Fixed object changed during fixture build')
        if Path(rel).name.startswith(('vt_operations_','vt_image_','vt_sh4_dispatch')):objects.append(ROOT/rel)
    fault_source=ROOT/'Sources/Bridge/vt_fixed_fault.cpp';fault_obj=out/'fault.o'
    cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',fault_source,fault_obj)
    with (out/'fault.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    objects.append(fault_obj)
    export=out/'exports.txt';export.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_original_case\n_vt_fixed_case\n_vt_guard_case\n')
    lib=out/'fixture.dylib';cmd=link_command({},lib)
    cmd=[('-Wl,-exported_symbols_list,'+str(export)) if x.startswith('-Wl,-exported_symbols_list,') else x for x in cmd]+[str(p) for p in objects]
    with (out/'link.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(lib));original_fn=dll.vt_original_case;fixed_fn=dll.vt_fixed_case
    original_fn.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint)]
    fixed_fn.argtypes=original_fn.argtypes+[ctypes.c_uint]
    left=ctypes.create_string_buffer(4096);right=ctypes.create_string_buffer(4096)
    le=(ctypes.c_uint*2)();ri=(ctypes.c_uint*2)();cases=0
    for i,word in enumerate(words):
        for seed in range(8):
            ln=original_fn(word,seed,left,le);rn=fixed_fn(i,seed,right,ri,0)
            if ln!=rn or left.raw[:ln]!=right.raw[:rn] or list(le)!=list(ri):
                raise RuntimeError(f'Pure SH4 execution mismatch word={word:04x}, seed={seed}, exceptions={list(le)}/{list(ri)}')
            cases+=1
    ln=original_fn(words[0],0,left,le);rn=fixed_fn(0,0,right,ri,1)
    if left.raw[:ln]==right.raw[:rn]:raise RuntimeError('Corrupted-context negative control did not fail')
    guard=dll.vt_guard_case;guard.argtypes=[ctypes.c_uint,ctypes.c_uint]
    if guard(0xe0000000,0x0009)!=1:raise RuntimeError('Unknown PC did not fail through controlled sticky exception')
    # At the BIOS reset PC the only authenticated word is the exact media
    # word. Choose a different word from that byte identity at test time.
    media=json.loads((ROOT/'Configuration/media.json').read_text())
    reset=int.from_bytes((ROOT/'build/assets/vtennis'/media['bios']).read_bytes()[:2],'little')
    if guard(0xa0000000,reset^0xffff)!=1:raise RuntimeError('Changed fixed instruction did not fail closed')
    report={'passed':True,'distinctWords':len(words),'contextsPerWord':8,'cases':cases,'instructionsPerCase':2,
        'sh4ClockHz':200000000,'interpreterCycleMultiplier':1,
        'compared':['complete pointer-free prepared Sh4Context bytes including all registers and cycle counter','SH4 exception PC and event'],
        'negativeControl':'One intentionally flipped fixed-context R0 bit is detected',
        'guardCases':['unknown PC gives sticky controlled FlycastException','changed BIOS reset word gives sticky controlled FlycastException'],
        'fixedManifestSHA256':sha(manifest_path),'referenceLibrarySHA256':sha(REFERENCE/'flycast_libretro.dylib'),
        'fixtureLibrarySHA256':sha(lib),'verifierSHA256':sha(__file__),
        'harnessSourceSHA256':{'original.cpp':sha(original),'fixed.cpp':sha(fixed),'vt_fixed_fault.cpp':sha(fault_source)},
        'limits':'Context-local operations only; no claim of complete bus/MMU/IRQ/delay-slot validation. Full original/native replay is reported separately.'}
    (ROOT/'Documentation/sh4-operation-acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
