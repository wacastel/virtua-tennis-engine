#!/usr/bin/env python3
"""Generate fixed, address-bound AICA ARM7 operations from verified ROM windows.

The input traces and RAM pages are private build artifacts, never public source.
Every aligned word in two verified immutable ROM windows is specialized offline.
Original observations validate these bindings; they never define instructions.
"""
from __future__ import annotations
import argparse, hashlib, json, struct, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import SOURCE
PINS = {
 'core/hw/arm7/arm7.cpp':'9a1e0193312f54d4de27bf5ff688917c28bb23ef991611e5426b1b50be802074',
 'core/hw/arm7/arm-new.h':'1e16e52b2bdb07538e0e25ffbe2e1c4568173902f0b3a3c86da0f6d11a2b75f8',
}
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def once(s,a,b):
 if s.count(a)!=1:raise ValueError('Changed ARM7 source anchor '+a[:60])
 return s.replace(a,b,1)
def generate(traces,output,source=SOURCE,chunk=64):
 raw={}
 for name,pin in PINS.items():
  p=source/name
  if sha(p)!=pin:raise ValueError('Unpinned ARM7 source '+name)
  raw[name]=p.read_text()
 entries=set(); observed=set(); inputs={}; pages=0
 configuration=ROOT/'Configuration/arm7-programs.json'
 recipe=json.loads(configuration.read_text())
 inputs[str(configuration.relative_to(ROOT))]=sha(configuration)
 for window in recipe['programWindows']:
  path=ROOT/'build/assets/vtennis'/window['name']
  if sha(path)!=window['fileSHA256']:raise ValueError('Changed immutable ARM source image')
  data=path.read_bytes()[window['offset']:window['offset']+window['bytes']]
  if len(data)!=window['bytes'] or hashlib.sha256(data).hexdigest()!=window['windowSHA256']:raise ValueError('Changed ARM program window')
  if window['bytes']%4 or window['mappedBase']%4:raise ValueError('Unaligned ARM program window')
  inputs[str(path.relative_to(ROOT))]=sha(path)
  entries.update((window['mappedBase']+i,struct.unpack_from('<I',data,i)[0]) for i in range(0,len(data),4))
 for trace in traces:
  inputs[str(trace.relative_to(ROOT)) if trace.is_relative_to(ROOT) else str(trace)]=sha(trace)
  records=[json.loads(x) for x in trace.read_text().splitlines() if x]
  page_map={}
  for row in records:
   if row['kind']=='page':
    path=Path(str(trace)+'.data')/row['file'];data=path.read_bytes()
    if len(data)!=4096 or row['offset']&4095:raise ValueError('Invalid captured page')
    page_map[row['number']]=(row['offset'],data);pages+=1
    inputs[str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)]=sha(path)
   elif row['kind']=='instruction':
    pc,word=row['pc'],row['opcode'];off,data=page_map[row['page']]
    if pc&3 or row['soundOffset']!=pc or not off<=pc<off+4096:raise ValueError('Unsupported ARM instruction mapping')
    if struct.unpack_from('<I',data,pc-off)[0]!=word:raise ValueError('Observation/page disagreement')
    observed.add((pc,word))
 if traces and not observed:raise ValueError('No genuine ARM7 execution observations')
 if not observed<=entries:raise ValueError('Captured coverage lost')
 output.mkdir(parents=True,exist_ok=True)
 for p in output.glob('arm7_*.cpp'):p.unlink()
 base=raw['core/hw/arm7/arm7.cpp']
 # Remove the unused original recompiler/interpreter fallback definition entirely.
 start=base.index('#if FEAT_AREC != DYNAREC_NONE\n//\n// Used by ARM7 Recompiler')
 end=base.index('#endif\t// FEAT_AREC != DYNAREC_NONE',start)+len('#endif\t// FEAT_AREC != DYNAREC_NONE')
 base=base[:start]+base[end:]
 for name in ('CPUSwitchMode','CPUUpdateFlags','CPUSoftwareInterrupt','CPUUndefinedException'):
  base=base.replace('static void '+name,'void '+name)
 base=once(base,'#include "arm7_rec.h"','#include "arm7_rec.h"\n#include "arm7_fixed.h"')
 base=once(base,'\t\tint& clockTicks = arm7ClockTicks;\n\t\t#include "arm-new.h"','\t\tvt_arm7_fixed_step();')
 base=base.replace('runInterpreter','runFixed')
 (output/'arm7_shell.cpp').write_text(base)
 # The original runtime ARM64 rotate masks a count of 32, but the C++ spelling
 # shifts by 32 at rotation zero. Constant specialization exposes that UB.
 # Use the defined rotate intrinsic, retaining every original flag operation.
 body=raw['core/hw/arm7/arm-new.h']
 for name,count in (('v',5),('value',1)):
  needle='(('+name+' << (32 - shift)) |\\\n              ('+name+' >> shift))'
  if body.count(needle)!=count:raise ValueError('Changed original ARM rotate expressions')
  body=body.replace(needle,'__builtin_rotateright32('+name+', unsigned(shift))')
 (output/'arm7_operation_body.h').write_text(body)
 prefix=raw['core/hw/arm7/arm7.cpp'].split('alignas(8) reg_pair')[0].split('namespace aica::arm\n{\n')[1]
 prefix+='\n#define N_FLAG (reg[RN_PSR_FLAGS].FLG.N)\n#define Z_FLAG (reg[RN_PSR_FLAGS].FLG.Z)\n#define C_FLAG (reg[RN_PSR_FLAGS].FLG.C)\n#define V_FLAG (reg[RN_PSR_FLAGS].FLG.V)\n'
 header='''#pragma once
#include "arm7.h"
#include "arm_mem.h"
namespace aica::arm {
void CPUSwitchMode(int,bool); void CPUUpdateFlags();
void CPUSoftwareInterrupt(int); void CPUUndefinedException();
extern u8 cpuBitsSet[256];
void vt_arm7_fixed_step();
'''+prefix+'''
template<u32 opcode> __attribute__((always_inline)) inline void vt_arm7_operation() {
 static_assert(__builtin_constant_p(opcode), "ARM opcode must be offline constant");
 armNextPC += 4;
 int &clockTicks = arm7ClockTicks;
 #define NO_OPCODE_READ
 #include "arm7_operation_body.h"
 #undef NO_OPCODE_READ
}
}
'''
 (output/'arm7_fixed.h').write_text(header)
 words=sorted({word for pc,word in entries})
 for index in range(0,len(words),chunk):
  lines=['#include "arm7_fixed.h"','namespace aica::arm {']
  for word in words[index:index+chunk]:
   lines.append(f'void vt_arm7_op_{word:08x}() {{ vt_arm7_operation<0x{word:08x}u>(); }}')
  lines.append('}')
  (output/f'arm7_ops_{index//chunk:04d}.cpp').write_text('\n'.join(lines)+'\n')
 sorted_entries=sorted(entries)
 if {pc for pc,word in sorted_entries}!=set(range(0,32768,4)):
  raise ValueError('ARM direct-PC slots require the two authenticated32KiB windows at0')
 buckets=[];position=0
 for pc in range(0,32768,4):
  first=position
  while position<len(sorted_entries) and sorted_entries[position][0]==pc:position+=1
  count=position-first
  if not 1<=count<=2 or first>65535:raise ValueError('Unexpected ARM slot variant bound')
  buckets.append((first,count))
 if position!=len(sorted_entries):raise ValueError('ARM bindings outside direct-PC slots')
 lines=['#include "arm7.h"','#include "arm_mem.h"','#include <algorithm>','#include "vt_fixed_fault.h"','namespace aica::arm {']
 lines += [f'void vt_arm7_op_{w:08x}();' for w in words]
 lines+=['struct FixedEntry { u32 pc,word; void(*operation)(); };','static const FixedEntry entries[] = {']
 lines += [f'{{0x{pc:08x}u,0x{word:08x}u,vt_arm7_op_{word:08x}}},' for pc,word in sorted_entries]
 lines+=['};','extern "C" const char *vt_fixed_arm7_marker() { return "Virtua Tennis fixed offline ARM7"; }',
  'struct FixedBucket { uint16_t first,count; };','static const FixedBucket buckets[8192] = {']
 lines += [f'{{{first}u,{count}u}},' for first,count in buckets]
 lines += ['};',r'''void vt_arm7_fixed_step() {
 const u32 pc=arm_Reg[R15_ARM_NEXT].I;
 u32 actual=0;
 if (!(pc&3) && pc<ARAM_SIZE) {
  actual=*(const u32*)&aica_ram[pc];
  if(pc<sizeof(buckets)/sizeof(buckets[0])*4u) {
   const FixedBucket bucket=buckets[pc>>2];
   const FixedEntry *entry=entries+bucket.first;
   const FixedEntry *end=entry+bucket.count;
   while(entry!=end) {
    if(entry->word==actual) { entry->operation(); return; }
    ++entry;
   }
  }
 }
 vt_fixed_fault("AICA ARM7",pc,actual,"Untranslated PC or changed instruction word");
}''','}']
 (output/'arm7_dispatch.cpp').write_text('\n'.join(lines)+'\n')
 report={'architecture':'Fixed PC/expected-word bindings to constant-opcode operations; original FIQ, banking, bus and sample scheduler',
  'sourcePins':PINS,'generatorSHA256':sha(__file__),'inputPins':inputs,'observedIdentities':len(observed),
  'capturedPages':pages,'fixedIdentities':len(entries),'uniqueOperations':len(words),
  'dispatch':{'kind':'constant direct-PC buckets over unchanged ordered fixed entries','slots':len(buckets),'singleVariantSlots':sum(count==1 for first,count in buckets),'twoVariantSlots':sum(count==2 for first,count in buckets),'orderedBindingSHA256':hashlib.sha256(b''.join(struct.pack('<II',pc,word)for pc,word in sorted_entries)).hexdigest(),'bucketSHA256':hashlib.sha256(b''.join(struct.pack('<HH',first,count)for first,count in buckets)).hexdigest(),'unalignedOrOutOfARAMWord':0,'alignedUnboundPCFetchesActualWord':True},
  'coverageLimit':'All aligned words in the two pinned 32KiB ROM windows; unknown PC or changed unadmitted word fails closed.',
  'programWindows':recipe['programWindows'],
  'definedSemanticsAdaptation':'Six original rotate expressions use __builtin_rotateright32 to preserve original ARM64 runtime rotation at count zero under offline constant specialization; original oracle remains unchanged.',
  'replaces':['core/hw/arm7/arm7.cpp'],
  'sources':{p.name:sha(p) for p in sorted(output.iterdir()) if p.suffix in ('.h','.cpp')}}
 save(output/'generation.json',report)
 print(json.dumps({k:report[k] for k in ('observedIdentities','fixedIdentities','uniqueOperations')}))
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trace',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,default=ROOT/'build/generated/arm7');p.add_argument('--source',type=Path,default=SOURCE);p.add_argument('--chunk-size',type=int,default=64);a=p.parse_args()
 if a.chunk_size<1:p.error('positive chunk size required')
 generate([x.resolve() for x in a.trace],a.output.resolve(),a.source.resolve(),a.chunk_size)
