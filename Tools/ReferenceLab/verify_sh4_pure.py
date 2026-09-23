#!/usr/bin/env python3
"""Actual original-operation vs prevalidated pure-segment mapped-RAM oracle."""
from pathlib import Path
import argparse,ctypes,hashlib,json,shutil,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'));sys.path.insert(0,str(ROOT/'scripts'))
from build_observer import SOURCE,REFERENCE,compile_command,link_command
from compile_sh4 import media_files,images
from sh4_fixture_inputs import add_fixture_arguments,fixture_inputs
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
rel=lambda p:str(Path(p).resolve().relative_to(ROOT))
p=argparse.ArgumentParser(description=__doc__);add_fixture_arguments(p,ROOT);p.add_argument('--smoke',action='store_true');a=p.parse_args();inputs=fixture_inputs(ROOT,a);candidate=inputs.include_directory;out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
cmfile=inputs.cpu_manifest if a.candidate is None else candidate/'manifest.json';cm=inputs.normalized;pf=inputs.plan;plan=json.loads(pf.read_text());cpuFile=inputs.cpu_manifest;cpu=inputs.cpu
if a.candidate is None and 'pureSegmentsHeader'not in cpu['runOptimization']:raise RuntimeError('Canonical pure-segment role missing')
if sha(cpuFile)!=cm['parentCPUManifestSHA256']or sha(pf)!=cm['planSHA256']:raise RuntimeError('Pure fixture input identity changed')
pins=dict(inputs.input_pins);pins.update({rel(cmfile):sha(cmfile),rel(pf):sha(pf),rel(cpuFile):sha(cpuFile),rel(__file__):sha(__file__),rel(Path(__file__).with_name('sh4_pure_fixture.cpp')):sha(Path(__file__).with_name('sh4_pure_fixture.cpp'))});objs=[]
for group in ['sources','objects']:
 for name,h in cm[group].items():
  path=candidate/name
  if sha(path)!=h:raise RuntimeError('Fixture input changed: '+name)
  pins[rel(path)]=h
  if group=='objects'and path not in [inputs.executor_object,Path(cm['mappedFetch']['replacementObject'])]:objs.append(path)
for name,h in cpu['objects'].items():
 path=ROOT/name;assert sha(path)==h,name
 if path.name.startswith(('vt_operations_','vt_image_','vt_sh4_dispatch')):objs.append(path);pins[rel(path)]=h
ims=images(*media_files());byname={n:(b,d)for n,b,d in ims};pure=plan['pureSegments'];rows=pure['segments']
if len(rows)!=16 or pure['firstFetchRemainsOriginal']is not True or pure['ordinaryBusEventCountsPreserved']is not False or pure['noHoistingAcrossMemoryOrControl']is not True or pure['originalOperationBodiesAndPerOperationCyclesRetained']is not True or pure['failedPreflightHasNoArchitecturalEffects']is not True:raise RuntimeError('Unreviewed pure-segment scope')
inc=[]
for r in rows:
 i=r['id'];base,data=byname[r['image']];pc=int(r['pc'],16);raw=data[pc-base:pc-base+2*r['instructions']];assert hashlib.sha256(raw).hexdigest()==r['instructionSpanSHA256'];words=struct.unpack('<'+'H'*r['instructions'],raw);mutants=[]
 for j,w in enumerate(words):
  at=pc+j*2;accepted={struct.unpack_from('<H',d,at-b)[0]for n,b,d in ims if b<=at< b+len(d)};mutants.append(next(m for m in [w^1,w^0x100,w^0x8000,0xffff,0]if m not in accepted))
 whole=next(b for b in plan['blocks']if b['pc']==r['parentBlockPC'])['instructions']==r['instructions']and r['firstInstructionIndex']==0;r['wholeBlock']=whole
 r['fixtureHelper']=r['probe']if a.candidate is not None else r['function']
 declaration=('extern "C" 'if a.candidate is not None else'')+'bool '+r['fixtureHelper']+'(Sh4Context*,Sh4Cycles&);'
 inc +=[declaration,f'static const u16 words_{i}[]={{'+','.join(hex(w)for w in words)+'};',f'static const u16 mutants_{i}[]={{'+','.join(hex(w)for w in mutants)+'};']
inc.append('static const Segment segments[]={'+','.join('{'+f'{r["pc"]},{r["instructions"]},{r["maximumExactCost"]},{str(r["wholeBlock"]).lower()},{r["fixtureHelper"]},words_{r["id"]},mutants_{r["id"]}'+'}'for r in rows)+'};')
(out/'segments.inc').write_text('\n'.join(inc)+'\n');source=out/'fixture.cpp';shutil.copy2(Path(__file__).with_name('sh4_pure_fixture.cpp'),source);shutil.copy2(__file__,out/'verifier-executed.py');obj=out/'fixture.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',source,obj);cmd[1:1]=['-I'+str(candidate/('block-overlay/core'if a.candidate is None else'overlay/core')),'-I'+str(candidate),'-I'+str(ROOT/'build/generated/sh4'),'-I'+str(out)]
with(out/'compile.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
objs.append(obj);fault=ROOT/'Sources/Bridge/vt_fixed_fault.cpp';fo=out/'fault.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',fault,fo)
with(out/'fault.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
objs.append(fo);pins[rel(fault)]=sha(fault);exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_pure_fixture_case\n');library=out/'fixture.dylib';cmd=link_command({cm['mappedFetch']['originalObjectTarget']:candidate/cm['mappedFetch']['replacementObject']},library);cmd=[('-Wl,-exported_symbols_list,'+str(exports))if v.startswith('-Wl,-exported_symbols_list,')else v for v in cmd]+list(map(str,objs))
with(out/'link.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
d=ctypes.CDLL(str(library));fn=d.vt_pure_fixture_case;fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32),ctypes.c_uint];fn.restype=ctypes.c_int
counts={};passed=hits=rejects=irqs=exceptions=0;perSegment=[0]*16
resultFile=(out/'results.jsonl').open('w')
def run(kind,x,expect=None,reject=False,irq=False):
 global passed,hits,rejects,irqs,exceptions
 data=(ctypes.c_uint32*14)(*[v&0xffffffff for v in x]);buf=(ctypes.c_uint32*24)();n=fn(data,buf,24);v=list(buf);good=n==24 and v[0]==1
 if expect is not None and not x[8]:good=good and v[1]==expect
 if reject:good=good and v[1]==0 and v[2]==1
 if irq:good=good and v[17]==1 and v[18]==1
 row={'group':kind,'parameters':x,'result':v};resultFile.write(json.dumps(row)+'\n');resultFile.flush()
 if not good:(out/'mismatch.json').write_text(json.dumps(row,indent=2)+'\n');raise RuntimeError(kind+' mismatch: '+str(x)+' -> '+str(v))
 counts[kind]=counts.get(kind,0)+1;passed+=1;hits+=v[1];perSegment[x[0]]+=v[1];rejects+=int(reject);irqs+=v[18];exceptions+=int(v[8]!=0)
def case(i,mode=0,budget=448,unit=2,seed=1,alias=0,mem=99999,fpscr=0,fd=0,condition=0,aux=0,irq=0,ratio=1,expected=1):return[i,mode,budget,seed,alias,unit,mem,fpscr,fd,condition,aux,irq,ratio,expected]
for r in rows:
 i=r['id'];maximum=r['maximumExactCost'];budgets=[maximum,maximum+1,448]if a.smoke else[-3,0,*range(1,449)]
 for b in budgets:
  active=b>maximum and b<=448;run('all_slice_budgets',case(i,budget=b,unit=(b+6)%6,seed=b+10,expected=int(active)),expect=int(active),reject=not active)
 if a.smoke:continue
 for unit in range(6):
  for alias in [0,0x80000000,0xa0000000]:
   for b in [maximum,maximum+1,448]:
    active=b>maximum;run('units_aliases_boundary',case(i,budget=b,unit=unit,alias=alias,expected=int(active)),expect=int(active),reject=not active)
 for fpscr in [0,1,0x80000,0x100000,0x200000,0x180001]:
  for fd in [0,1]:run('original_FPU_prefix_exceptions',case(i,fpscr=fpscr,fd=fd,seed=fpscr+7),expect=None)
 for cond in list(range(1,22)):
  run('admission_no_effects',case(i,condition=cond,expected=0),expect=0,reject=True)
 for ratio in [0,2,8]:run('ratio_no_effects',case(i,ratio=ratio,expected=0),expect=0,reject=True)
 for word in range(r['instructions']):
  run('every_changed_word_no_early_effect',case(i,condition=22,aux=word,expected=0),expect=0,reject=True)
  run('actual_fixed_late_word_rejection',case(i,mode=3,condition=22,aux=word,expected=0))
 for cond in [2,16,24,25,26]:
  for pos in [1,2,r['instructions']]:run('callback_fallback_order_and_faults',case(i,mode=1,condition=cond,aux=pos,expected=0))
 for b in [0,1,maximum,maximum+1,448]:run('actual_segment_fallback',case(i,mode=1,budget=b))
 run('unrelated_RAM_mask',case(i,condition=27),expect=1)
 for unit in range(6):run('same_boundary_original_INTC',case(i,unit=unit,irq=1),expect=1,irq=True)
 if r['wholeBlock']:
  for b in [-3,0,1,maximum,maximum+1,448]:
   for fd in [0,1]:run('actual_block_selector',case(i,mode=2,budget=b,fd=fd))
resultFile.close();assert all(perSegment)
for n,h in pins.items():assert sha(ROOT/n)==h,n
report={'passed':True,'cases':passed,'successfulBatches':hits,'perSegmentSuccessfulBatches':perSegment,'negativeNoEffectRows':rejects,'originalIRQDeliveries':irqs,'matchingExceptionRows':exceptions,'groups':counts,'inputPins':pins,'identityKind':cm['identityKind'],'nativeCPUManifestSHA256':sha(cpuFile),**({'engineSHA256':cm['productSHA256']}if 'productSHA256'in cm else{}),'fixtureLibrarySHA256':sha(library),'resultsSHA256':sha(out/'results.jsonl'),'scope':['All512 architectural context bytes, cycle lastUnit/memOps/ratio and full32MiB RAM compare against original operations at each admitted/rejected batch exit.','Actual helper functions compiled into the six changed production block TUs are directly invoked; complete pure blocks also use the actual Run selector.','Callback fallback preserves every recorded fetch/PC/cycle event; admitted batches intentionally omit pure RAM fetch callbacks under original-reader guards.','IRQ tests advance both equal post-segment cycle states to the same unchanged448-cycle boundary before calling original UpdateSystem_INTC; they are not independent long gameplay tests.','Changed-word negative oracle stops before that fetched instruction; candidate must fail fixed admission at the identical prefix, never from preflight.'],'unrelatedCompositeObjectOmitted':cm.get('unrelatedCompositeObjectOmitted')}
(out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k]for k in ['passed','cases','successfulBatches','negativeNoEffectRows','originalIRQDeliveries','matchingExceptionRows']}))
