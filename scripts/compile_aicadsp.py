#!/usr/bin/env python3
"""Generate literal AICA DSP steps and exact whole-program guards.

One verified cartridge image roots all ascending load/clear prefixes. Optional
private original-CPU traces verify that observed uploads lie within this set.
The original runtime instruction decoder and instruction loop are not linked.
"""
from __future__ import annotations
import argparse,hashlib,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import SOURCE
PINS={
 'core/hw/aica/dsp.cpp':'73f68333463766d6ebcf17484570525d9ed9425b98a06fcf35b64dfd9538bf06',
 'core/hw/aica/dsp_interp.cpp':'7e6bb74070726c64287a0350b89767e8eb0ce7bd5c2375f2f5f1cef2dea3f3d4',
}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def replace(s,a,b):
 if s.count(a)!=1:raise ValueError('Changed AICA DSP anchor '+a[:60])
 return s.replace(a,b,1)
def hash_words(words):
 h=14695981039346656037
 for w in words:h=((h^w)*1099511628211)&0xffffffffffffffff
 return h

def generate(traces,output,source=SOURCE,chunk=64):
 original={}
 for name,pin in PINS.items():
  path=source/name
  if sha(path)!=pin:raise ValueError('Unpinned DSP source '+name)
  original[name]=path.read_text()
 sampled=set();observed=set();inputs={}
 configuration=ROOT/'Configuration/aicadsp-program.json';recipe=json.loads(configuration.read_text())
 media=ROOT/'build/assets/vtennis'/recipe['name']
 if sha(media)!=recipe['fileSHA256']:raise ValueError('Changed DSP cartridge source')
 full=media.read_bytes()[recipe['offset']:recipe['offset']+recipe['bytes']]
 if len(full)!=2048 or hashlib.sha256(full).hexdigest()!=recipe['programSHA256']:raise ValueError('Changed DSP program window')
 words=struct.unpack('<512I',full);programs=set()
 for count in range(513):
  programs.add(struct.pack('<512I',*(words[:count]+(0,)*(512-count))))
  programs.add(struct.pack('<512I',*((0,)*count+words[count:])))
 inputs[str(configuration.relative_to(ROOT))]=sha(configuration)
 inputs[str(media.relative_to(ROOT))]=sha(media)
 for trace in traces:
  inputs[str(trace.relative_to(ROOT)) if trace.is_relative_to(ROOT) else str(trace)]=sha(trace)
  by_id={}
  for row in (json.loads(x) for x in trace.read_text().splitlines() if x):
   if row['kind']=='program':
    path=Path(str(trace)+'.data')/row['file'];raw=path.read_bytes()
    if len(raw)!=2048:raise ValueError('Invalid MPRO image size')
    if raw not in programs:raise ValueError('Original DSP upload outside pinned load/clear closure')
    by_id[row['number']]=raw;observed.add(raw)
    inputs[str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)]=sha(path)
   elif row['kind']=='sample-program':sampled.add(by_id[row['program']])
 if traces and not sampled:raise ValueError('No genuine sampled DSP programs')
 programs=sorted(programs,key=lambda b:(hash_words(struct.unpack('<512I',b)),b))
 operations=sorted({(step,*struct.unpack_from('<4I',p,step*16)) for p in programs for step in range(128)})
 op_ids={op:i for i,op in enumerate(operations)}
 output.mkdir(parents=True,exist_ok=True)
 for p in output.glob('aicadsp_*.cpp'):p.unlink()
 common=original['core/hw/aica/dsp.cpp']
 start=common.index('void DecodeInst(');end=common.index('#if FEAT_DSPREC == DYNAREC_NONE',start)
 common=common[:start]+common[end:]
 common=replace(common,'#include "aica.h"','#include "aica.h"\n#include "aicadsp_fixed.h"')
 common=replace(common,'void recompile() {\n}','void recompile() {\n vt_aicadsp_select();\n}')
 # Validation precedes original dirty clearing and stopped detection, including zero image.
 common=replace(common,'\tif (state.dirty)\n\t{','\tif (state.dirty)\n\t{\n\t\tvt_aicadsp_select();')
 (output/'aicadsp_common.cpp').write_text(common)
 interp=original['core/hw/aica/dsp_interp.cpp']
 start=interp.index('\t\tu32 *IPtr =');end=interp.index('\n\t}\n\t--state.MDEC_CT;',start)
 body=interp[start:end]
 body=replace(body,'\t\tu32 *IPtr = DSPData->MPRO + step * 4;', '\t\tconstexpr u32 IPtr[4]={w0,w1,w2,w3};')
 body=replace(body,'\t\t\tcontinue;','\t\t\treturn;')
 variables=['ACC','SHIFTED','X','Y','B','INPUTS','MEMVAL','FRC_REG','Y_REG','ADRS_REG']
 header=interp[:interp.index('#include "build.h"')]+'''#pragma once
#include "hw/aica/dsp.h"
#include "hw/aica/aica.h"
#include "hw/aica/aica_if.h"
namespace aica::dsp {
void vt_aicadsp_select();
struct FixedScratch {
 s32 ACC=0,SHIFTED=0,X=0,Y=0,B=0,INPUTS=0,MEMVAL[4]={0},FRC_REG=0,Y_REG=0;
 u32 ADRS_REG=0;
};
template<int step,u32 w0,u32 w1,u32 w2,u32 w3>
__attribute__((always_inline)) inline void vt_aicadsp_operation(FixedScratch &s) {
 static_assert(__builtin_constant_p(w0+w1+w2+w3+step),"DSP operation must be fixed offline");
'''
 header+='\n'.join(f' auto &{v}=s.{v};' for v in variables)+'\n'+body+'\n}\n}\n'
 (output/'aicadsp_fixed.h').write_text(header)
 for index in range(0,len(operations),chunk):
  lines=['#include "aicadsp_fixed.h"','namespace aica::dsp {']
  for i,op in enumerate(operations[index:index+chunk],index):
   step,*ws=op
   lines.append(f'void vt_aicadsp_op_{i}(FixedScratch &s) {{ vt_aicadsp_operation<{step},'+','.join(f'0x{w:08x}u' for w in ws)+'>(s); }')
  lines+=['}']
  (output/f'aicadsp_ops_{index//chunk:04d}.cpp').write_text('\n'.join(lines)+'\n')
 lines=['#include "aicadsp_fixed.h"','#include "vt_fixed_fault.h"','#include <algorithm>','#include <cstring>','namespace aica::dsp {']
 lines += [f'void vt_aicadsp_op_{i}(FixedScratch&);' for i in range(len(operations))]
 lines+=['using Operation=void(*)(FixedScratch&);','struct FixedProgram { uint64_t hash; u32 words[512]; Operation steps[128]; };','static const FixedProgram programs[] = {']
 for p in programs:
  words=struct.unpack('<512I',p)
  ids=[op_ids[(step,*words[step*4:step*4+4])] for step in range(128)]
  lines.append('{0x%016xULL,{%s},{%s}},'%(hash_words(words),','.join(f'0x{w:08x}u' for w in words),','.join(f'vt_aicadsp_op_{i}' for i in ids)))
 lines += ['};',r'''
extern "C" const char *vt_fixed_aicadsp_marker() { return "Virtua Tennis fixed offline AICA DSP"; }
static const FixedProgram *selected=nullptr;
void vt_aicadsp_select() {
 uint64_t hash=14695981039346656037ULL;
 for(u32 word:DSPData->MPRO) hash=(hash^word)*1099511628211ULL;
 const auto *p=std::lower_bound(std::begin(programs),std::end(programs),hash,
  [](const FixedProgram &p,uint64_t h){return p.hash<h;});
 for(;p!=std::end(programs) && p->hash==hash;++p)
  if(!std::memcmp(p->words,DSPData->MPRO,sizeof(p->words))) { selected=p; return; }
 vt_fixed_fault("AICA DSP",0x3400,u32(hash),"Unknown complete MPRO image");
}
void runStep() {
 if(state.stopped) return;
 if(!selected || std::memcmp(selected->words,DSPData->MPRO,sizeof(selected->words)))
  vt_aicadsp_select();
 FixedScratch s;
 for(Operation operation:selected->steps) operation(s);
 --state.MDEC_CT;
 if(state.MDEC_CT==0) state.MDEC_CT=state.RBL+1;
}
}''']
 (output/'aicadsp_dispatch.cpp').write_text('\n'.join(lines)+'\n')
 report={'architecture':'Exact complete MPRO identity -> 128 fixed step functions, with literal bitfields and original data/RAM timing',
  'sourcePins':PINS,'generatorSHA256':sha(__file__),'inputPins':inputs,'admittedPrograms':len(programs),'sampledPrograms':len(sampled),'observedUploadImages':len(observed),'uniqueStepOperations':len(operations),
  'coverageLimit':'Every ascending load/clear prefix of the pinned cartridge program; all other full MPRO images fail closed. No runtime instruction decode.',
  'programRecipe':recipe,
  'replaces':['core/hw/aica/dsp.cpp','core/hw/aica/dsp_interp.cpp'],
  'programSHA256s':[hashlib.sha256(p).hexdigest() for p in programs],
  'sources':{p.name:sha(p) for p in sorted(output.iterdir()) if p.suffix in ('.cpp','.h')}}
 save(output/'generation.json',report);print(json.dumps({k:report[k] for k in ('admittedPrograms','sampledPrograms','uniqueStepOperations')}));return report
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trace',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,default=ROOT/'build/generated/aicadsp');p.add_argument('--source',type=Path,default=SOURCE);p.add_argument('--chunk-size',type=int,default=64);a=p.parse_args()
 if a.chunk_size<1:p.error('positive chunk size required')
 generate([x.resolve() for x in a.trace],a.output.resolve(),a.source.resolve(),a.chunk_size)
