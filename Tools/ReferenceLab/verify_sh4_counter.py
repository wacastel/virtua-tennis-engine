#!/usr/bin/env python3
"""Actual-main-RAM differential test for the guarded fixed SH4 counter-loop contraction."""
from pathlib import Path
import argparse,ctypes,hashlib,json,re,shutil,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from compile_sh4 import media_files,images,brace_end,metadata
from build_observer import SOURCE,REFERENCE,compile_command,link_command
from sh4_fixture_inputs import add_fixture_arguments,fixture_inputs

def sha(path):
    with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def rel(path):return str(Path(path).resolve().relative_to(ROOT))
def main():
    ap=argparse.ArgumentParser(description=__doc__);add_fixture_arguments(ap,ROOT)
    ap.add_argument('--smoke',action='store_true',help='Development smoke only; never final acceptance.');a=ap.parse_args();inputs=fixture_inputs(ROOT,a);candidate=inputs.include_directory;out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    cmfile=inputs.cpu_manifest if a.candidate is None else candidate/'manifest.json';cm=inputs.normalized;planfile=inputs.plan;plan=json.loads(planfile.read_text());nodes=plan['guardedChain']['nodes']
    if a.candidate is None and not all(k in inputs.cpu['runOptimization'] for k in ['counterLoopHeader','counterLoopSource']):raise RuntimeError('Canonical counter-loop roles missing')
    counter=plan.get('counterLoop',{})
    for key,value in {'entryPC':'0x0c0c183a','maximumIterations':14,'instructionsPerIteration':33,'memoryOperationsPerIteration':14,'cyclesPerIteration':30,'completeContinuingIterationsOnly':True,'cyclesAfterAtLeast':1,'counterAfterAtLeast':1,'busEventCountsPreserved':False,'originalFallbackUnchanged':True}.items():
        if counter.get(key)!=value:raise RuntimeError('Unreviewed counter-loop scope: '+key)
    cpufile=inputs.cpu_manifest;cpu=inputs.cpu
    if sha(cpufile)!=cm['parentCPUManifestSHA256']or sha(planfile)!=cm['planSHA256']:raise RuntimeError('Candidate identity differs')
    pins=dict(inputs.input_pins);pins.update({rel(cmfile):sha(cmfile),rel(planfile):sha(planfile),rel(cpufile):sha(cpufile),rel(Path(__file__)):sha(__file__),rel(Path(__file__).with_name('sh4_counter_fixture.cpp')):sha(Path(__file__).with_name('sh4_counter_fixture.cpp'))});objects=[]
    mapped=cm['mappedFetch'];replacements={mapped['originalObjectTarget']:candidate/mapped['replacementObject']}
    for group in ['sources','objects']:
        for name,digest in cm[group].items():
            p=candidate/name
            if sha(p)!=digest:raise RuntimeError('Candidate changed: '+name)
            pins[rel(p)]=digest
            if group=='objects'and p not in [inputs.executor_object,Path(mapped['replacementObject'])]:objects.append(p)
    if inputs.cpu.get('runOptimization',{}).get('counterLoopSource') and not any(p.name=='counter_loop.o' for p in objects):raise RuntimeError('Actual counter-loop helper object missing from fixture link')
    for name,digest in cpu['objects'].items():
        p=ROOT/name
        if sha(p)!=digest:raise RuntimeError('Fixed object changed')
        if p.name.startswith(('vt_operations_','vt_image_','vt_sh4_dispatch')):objects.append(p);pins[rel(p)]=digest
    files,info=media_files();ims=images(files,info);image_by_name={n:(b,d)for n,b,d in ims};programs=plan['blocks']+plan['runEntryOverrides'];words={}
    expected=[0xc0c183a,0xc0bf320,0xc0be080,0xc0bf33a,0xc0c183e,0xc0c184e]
    if [int(n['pc'],16)for n in nodes]!=expected:raise RuntimeError('Unexpected chain graph')
    for i,node in enumerate(nodes):
        if int(node['nextPC'],16)!=expected[(i+1)%6]:raise RuntimeError('Changed chain edge')
        p=next(p for p in programs if p['pc']==node['pc']);base,data=image_by_name[p['image']];code=data[p['sourceOffset']:p['sourceOffset']+2*p['instructions']]
        if hashlib.sha256(code).hexdigest()!=node['sha256']:raise RuntimeError('Original chain span mismatch')
        if p.get('terminalIndirectCall')or p.get('terminalReturn'):code+=data[p['sourceOffset']+len(code):p['sourceOffset']+len(code)+2]
        for j in range(len(code)//2):words[expected[i]+2*j]=int.from_bytes(code[j*2:j*2+2],'little')
    if len(words)!=33:raise RuntimeError('Counter loop must contain exactly 33 original instructions including slots')
    game_base,game=image_by_name['game0']
    if struct.unpack_from('<I',game,0xc0bf360-game_base)[0]!=0xc28a25c:raise RuntimeError('Original dispatch-table literal differs')
    include='static const uint32_t chainNodes[]={'+','.join(hex(pc)for pc in expected)+'};\nstatic const ChainWord chainWords[]={'+','.join('{'+hex(pc)+','+hex(w)+'}'for pc,w in sorted(words.items()))+'};\n'
    (out/'chain_words.inc').write_text(include);shutil.copy2(Path(__file__),out/'verifier-executed.py');src=out/'fixture.cpp';shutil.copy2(Path(__file__).with_name('sh4_counter_fixture.cpp'),src)
    obj=out/'fixture.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',src,obj);cmd[1:1]=['-I'+str(out),'-I'+str(candidate),'-I'+str(ROOT/'build/generated/sh4')]
    with(out/'compile.log').open('w')as f:subprocess.run(cmd,cwd=REFERENCE,stdout=f,stderr=subprocess.STDOUT,check=True)
    objects.append(obj)
    original=SOURCE/'core/hw/sh4/interpr/sh4_interpreter.cpp';text=original.read_text();native=inputs.executor_source.read_text()
    marker='void Sh4Interpreter::ExecuteOpcode(u16 op)\n{';start=native.index(marker)+len(marker)-1;body=native[start+1:brace_end(native,start)-1]
    if re.sub(r'\s+','',body)!='vt_sh4_execute(ctx,op,sh4cycles);':raise RuntimeError('Unexpected fixed dispatcher')
    for method in ['ExecuteDelayslot','ReadNexOp']:
        def get(s):
            pos=s.index('Sh4Interpreter::'+method+'(');pos=s.index('{',pos);return s[pos:brace_end(s,pos)]
        if get(text)!=get(native):raise RuntimeError('Original lifecycle changed')
    derived=text.replace(marker,'#include "vt_sh4_fixed.h"\nextern bool vt_fixture_fixed_delay;\n'+marker+'\n if(vt_fixture_fixed_delay){'+body+'return;}',1)
    dsrc=out/'delay-interpreter.cpp';dsrc.write_text(derived);dobj=out/'delay-interpreter.o';cmd,target=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',dsrc,dobj);cmd[1:1]=['-I'+str(ROOT/'build/generated/sh4')]
    with(out/'delay-compile.log').open('w')as f:subprocess.run(cmd,cwd=REFERENCE,stdout=f,stderr=subprocess.STDOUT,check=True)
    replacements[target]=dobj;pins[rel(original)]=sha(original)
    fault=ROOT/'Sources/Bridge/vt_fixed_fault.cpp';fobj=out/'fault.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',fault,fobj)
    with(out/'fault.log').open('w')as f:subprocess.run(cmd,cwd=REFERENCE,stdout=f,stderr=subprocess.STDOUT,check=True)
    objects.append(fobj);pins[rel(fault)]=sha(fault)
    exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_counter_fixture_case\n');lib=out/'fixture.dylib';cmd=link_command(replacements,lib)
    cmd=[('-Wl,-exported_symbols_list,'+str(exports))if v.startswith('-Wl,-exported_symbols_list,')else v for v in cmd]+[str(p)for p in objects]
    with(out/'link.log').open('w')as f:subprocess.run(cmd,cwd=REFERENCE,stdout=f,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(lib));fn=dll.vt_counter_fixture_case
    fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32),ctypes.c_uint];fn.restype=ctypes.c_int
    counts={};batches=iterations=irq_deliveries=guard_rows=0;rows=[]
    def check(kind,params,expect_batch=None,expect_reject=False,expect_irq=False):
        nonlocal batches,iterations,irq_deliveries,guard_rows
        params=[v&0xffffffff for v in params];data=(ctypes.c_uint32*12)(*params);buf=(ctypes.c_uint32*24)()
        n=fn(data,buf,24);values=list(buf)
        good=n==24 and values[0]==1
        if params[0]==1:good=good and values[8]in(0,1) and values[9]in(0,1)
        if expect_batch is not None:good=good and values[1]==(expect_batch>0) and values[2]==expect_batch
        if expect_reject:good=good and values[1]==0 and values[3]==1
        if expect_irq:good=good and values[16]==1 and values[17]==1 and values[18]!=0
        row={'group':kind,'parameters':params,'result':values}
        if not good:
            (out/'mismatch.json').write_text(json.dumps(row,indent=2)+'\n');raise RuntimeError('Actual-RAM mismatch: '+kind+' '+str(params)+' => '+str(values))
        counts[kind]=counts.get(kind,0)+1;batches+=values[1];iterations+=values[2];irq_deliveries+=values[17];guard_rows+=int(expect_reject)
        if len(rows)<16 or expect_irq and irq_deliveries<=8:rows.append(row)
    def batch(budget,count,alias=0,mem=4,unit=2,ratio=1,condition=0,aux=0,seed=1):
        return [0,budget,count,seed,alias,mem,unit,ratio,condition,aux,0, min(14,(budget-1)//30,count-1)]
    # Independent immediate batch exits: every allowed scheduler budget, counter bounds, exact metadata, aliases.
    budgets=[31,32,60,61,421,448] if a.smoke else list(range(1,449))
    for budget in budgets:
        for count in ([2,15,100] if a.smoke else [0,1,2,14,15,0x7fffffff,0xffffffff]):
            n=min(14,(budget-1)//30,count-1) if budget>=31 and 2<=count<=0x7fffffff else 0
            p=batch(budget,count);p[-1]=n;check('every_budget_counter_batch_boundary',p,expect_batch=n,expect_reject=n==0)
    for alias in [0,0x80000000,0xa0000000]:
        for budget in [31,61,448]:
            for mem in [4,1025,1000000,0x7fffffff-196]:
                p=batch(budget,100,alias,mem,seed=(mem+budget)&255);check('large_metadata_and_alias_batch',p,expect_batch=p[-1])
    for condition in [15,43,44]:
        p=batch(448,100,condition=condition);check('mapped_data_aliases_and_unrelated_mask',p,expect_batch=14)
    if not a.smoke:
        for budget in [31,61,448]:
            p=batch(budget,100);p[10]=1;check('pending_IRQ_batch_stays_before_boundary',p,expect_batch=p[-1])
        for budget in [0,-3]:
            check('initial_do_while_instruction_then_IRQ',[1,budget,100,1,0,4,2,1,0,0,1,0],expect_irq=True)
        # Full instruction exit at exactly original scheduler/IRQ delivery boundary, with the actual selector.
        for budget in range(1,449):
            for irq in [0,1]:
                p=[1,budget,1000,budget,0,4,2,1,0,0,irq,0];check('every_448_slice_boundary_actual_INTC',p,expect_irq=irq==1)
        for alias in [0x80000000,0xa0000000]:
            for count in [0,1,2,14,15,0xffffffff]:
                for budget in [1,30,31,61,448]:check('natural_final_iteration_and_alias_fallback',[1,budget,count,7,alias,4,2,1,0,0,0,0])
        # Any rejected admission must be completely effect free at the already-fetched boundary.
        for cond in [1,2,3,4,5,6,7,8,9,10,11,12,13,14,16,17,18,19,20,21,23,24,25,26,27,28,29,30,31,33,34,35,36,37,38,39,40,41,42,45,46]:
            p=batch(448,100,condition=cond);p[0]=2;p[-1]=0;check('safety_preflight_no_effects',p,expect_batch=0,expect_reject=True)
        for word in range(len(words)):
            p=batch(448,100,condition=32,aux=word);p[0]=2;p[-1]=0;check('all_33_code_words_rejected_without_effects',p,expect_batch=0,expect_reject=True)
        for mem in [0,1,2,3,0xffffffff,0x80000000,0x7fffffff-195,0x7fffffff]:
            p=batch(448,100,mem=mem);p[0]=2;p[-1]=0;check('cold_negative_and_overflow_metadata',p,expect_batch=0,expect_reject=True)
        for unit in [0,1,3,4,5]:
            p=batch(448,100,unit=unit);p[0]=2;p[-1]=0;check('nonsteady_pairing_fallback',p,expect_batch=0,expect_reject=True)
        for ratio in [0,2,8]:
            p=batch(448,100,ratio=ratio);p[0]=2;p[-1]=0;check('nonunit_CPU_ratio_fallback',p,expect_batch=0,expect_reject=True)
        for budget in [0,0xffffffff,449,10000]:
            p=batch(budget,100);p[0]=2;p[-1]=0;check('outside_timeslice_budget_fallback',p,expect_batch=0,expect_reject=True)
        # Excluded readers/device handlers execute the original callback events and exceptions via unchanged fallback.
        for cond in [2,3,4,18]:
            for effect in [0,1,2,4]:
                for alias in [0,0x80000000,0xa0000000]:check('callback_fallback_event_state_memory_parity',[3,448,20,3,alias,4,2,1,cond,effect,0,0])
    for path,digest in pins.items():
        if sha(ROOT/path)!=digest:raise RuntimeError('Proof input changed: '+path)
    report={'passed':True,'smokeOnly':a.smoke,'inputMode':cm['identityKind'],'cases':sum(counts.values()),'caseGroups':counts,'acceptedBatchCount':batches,'acceptedCompleteIterations':iterations,'rejectedNoEffectsRows':guard_rows,'actualInterruptDeliveries':irq_deliveries,'sampleRows':rows,
      'compared':['all512SH4contextBytes','exact cycle_counter, lastUnit, memOps, ratio','all32MiB actual main RAM bytes','actual UpdateSystem_INTC pending IRQ at unchanged448cycle boundary','ordered callbacks and exception/Stop behavior in excluded fallback cases'],
      'inputPins':pins,'fixtureSourceSHA256':sha(src),'fixtureLibrarySHA256':sha(lib),'wordIncludeSHA256':sha(out/'chain_words.inc'),'originalDelayDispatchCopySHA256':sha(dsrc),
      'limits':'Synthetic plain-RAM prepared loop states, not a gameplay or performance test. Successful contraction intentionally elides ordinary RAM fetch/read events; those counts are not claimed equal. Four final RAM writes and full RAM are compared. Full device scheduling is excluded by choosing no device event before the tested boundary; actual original pending IRQ delivery is exercised. Existing mapping, full-engine replay and realtime tests remain separate.'}
    (out/'acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k]for k in ['passed','smokeOnly','cases','caseGroups','acceptedBatchCount','acceptedCompleteIterations','rejectedNoEffectsRows','actualInterruptDeliveries']}),flush=True)
if __name__=='__main__':main()
