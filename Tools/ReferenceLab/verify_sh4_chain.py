#!/usr/bin/env python3
"""Actual-object differential test for the bounded six-node SH4 Run chain."""
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
    a=ap.parse_args();inputs=fixture_inputs(ROOT,a);candidate=inputs.include_directory;out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    cmfile=inputs.cpu_manifest if a.candidate is None else candidate/'manifest.json';cm=inputs.normalized;planfile=inputs.plan;plan=json.loads(planfile.read_text());nodes=plan['guardedChain']['nodes']
    cpufile=inputs.cpu_manifest;cpu=inputs.cpu
    if sha(cpufile)!=cm['parentCPUManifestSHA256']or sha(planfile)!=cm['planSHA256']:raise RuntimeError('Candidate identity differs')
    pins=dict(inputs.input_pins);pins.update({rel(cmfile):sha(cmfile),rel(planfile):sha(planfile),rel(cpufile):sha(cpufile),rel(Path(__file__)):sha(__file__),rel(Path(__file__).with_name('sh4_chain_fixture.cpp')):sha(Path(__file__).with_name('sh4_chain_fixture.cpp'))});objects=[]
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
    game_base,game=image_by_name['game0']
    if struct.unpack_from('<I',game,0xc0bf360-game_base)[0]!=0xc28a25c:raise RuntimeError('Original dispatch-table literal differs')
    include='static const uint32_t chainNodes[]={'+','.join(hex(pc)for pc in expected)+'};\nstatic const ChainWord chainWords[]={'+','.join('{'+hex(pc)+','+hex(w)+'}'for pc,w in sorted(words.items()))+'};\n'
    (out/'chain_words.inc').write_text(include);shutil.copy2(Path(__file__),out/'verifier-executed.py');src=out/'fixture.cpp';shutil.copy2(Path(__file__).with_name('sh4_chain_fixture.cpp'),src)
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
    exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_chain_fixture_case\n');lib=out/'fixture.dylib';cmd=link_command(replacements,lib)
    cmd=[('-Wl,-exported_symbols_list,'+str(exports))if v.startswith('-Wl,-exported_symbols_list,')else v for v in cmd]+[str(p)for p in objects]
    with(out/'link.log').open('w')as f:subprocess.run(cmd,cwd=REFERENCE,stdout=f,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(lib));fn=dll.vt_chain_fixture_case;fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p,ctypes.c_uint];fn.restype=ctypes.c_int;buf=ctypes.create_string_buffer(1<<20);counts={};guards=0
    def run(params,native):
        data=(ctypes.c_uint32*11)(*[v&0xffffffff for v in [*params,native]]);size=fn(data,buf,len(buf))
        if size<=0:raise RuntimeError('Fixture failed '+str(size)+' '+str(params))
        return ctypes.string_at(buf,size)
    def compare(kind,params):
        left=run(params,0);right=run(params,1)
        if left!=right:
            (out/'mismatch-original.bin').write_bytes(left);(out/'mismatch-native.bin').write_bytes(right)
            (out/'mismatch.json').write_text(json.dumps({'kind':kind,'parameters':params,'originalHeader':struct.unpack('<13I',left[:52]),'nativeHeader':struct.unpack('<13I',right[:52]),'firstByte':next((i for i,(a,b)in enumerate(zip(left,right))if a!=b),None)},indent=2)+'\n')
            raise RuntimeError('Chain mismatch: '+kind+' '+str(params))
        h=struct.unpack('<13I',left[:52])
        if h[0]in(4,5)or h[8]:raise RuntimeError('Unexpected failure/speculative read in admitted case')
        counts[kind]=counts.get(kind,0)+1;return left,h
    proof=[]
    for loops in [1,2,5]:
        raw,h=compare('complete_original_counter_loops',[0,loops,10000,0,0,0,1,-1,0,0])
        if h[0]!=1 or h[4]!=0xc0c1858 or h[9]!=0:raise RuntimeError('Original counter did not complete')
        off=52+512;count=struct.unpack_from('<I',raw,off)[0];events=[struct.unpack_from('<5IQ',raw,off+4+i*28)for i in range(count)]
        visits={hex(pc):sum(e[0]in(0,4)and(e[1]&0x1fffffff)==pc for e in events)for pc in expected}
        if any(v!=loops for v in visits.values()):raise RuntimeError('Full chain nodes were not traversed once per loop')
        proof.append({'loops':loops,'nodeVisits':visits,'fetches':h[5],'dataAccesses':h[6],'consumedCycles':10000-ctypes.c_int32(h[3]).value})
    for entry in range(6):
        for budget in [-3,0,*range(1,100),1000]:
            for alias in [0,0x80000000,0xa0000000]:compare('all_entries_aliases_cycle_boundaries',[entry,3,budget,alias,0,0,1,-1,0,0])
        for flag in [1,2,3,4,8,16,32,64,8192,16384,65536]:
            for alias in [0,0x80000000,0xa0000000]:compare('live_data_reader_and_control_exits',[entry,3,10000,alias,flag,0,1,-1,0,0])
    opcode_metadata=metadata(SOURCE)
    for edge in range(6):
        pc=expected[(edge+1)%6];word=words[pc]
        admitted={int.from_bytes(d[pc-b:pc-b+2],'little')for n,b,d in ims if b<=pc<b+len(d)}
        unknown=next(w for w in range(65536)if w not in admitted)
        alternate=next((w for w in sorted(admitted) if w!=word and opcode_metadata[w]['handler']!='iNotImplemented'),None)
        if alternate is None:raise RuntimeError('No implemented admitted edge-word alternate')
        for occurrence in [1,2]:
            for flag in [1024,2048,128]:compare('edge_fault_stop_and_valid_PC_change',[0,5,10000,0,flag,edge,occurrence,-1,pc|0x80000000,0])
            compare('edge_legitimate_word_fallback',[0,5,10000,0,512,edge,occurrence,-1,0,alternate])
            for flag,value in [(256,0),(512,unknown)]:
                result=run([0,5,10000,0,flag,edge,occurrence,-1,0,value],1);h=struct.unpack('<13I',result[:52])
                if h[0]!=4:raise RuntimeError('Edge PC/word change did not fail closed')
                guards+=1
    accesses=proof[-1]['dataAccesses']
    for fault in range(accesses):compare('every_data_access_exception',[0,5,10000,0,0,0,1,fault,0,0])
    for path,digest in pins.items():
        if sha(ROOT/path)!=digest:raise RuntimeError('Proof input changed: '+path)
    report={'passed':True,'cases':sum(counts.values()),'caseGroups':counts,'failClosedCases':guards,'completeLoopProof':proof,'actualSelectorAndObjects':True,'inputMode':cm['identityKind'],'preparedStates':'Each of six starts is obtained by original instruction execution from the same prepared register/stack/function-table state; all original PR stack save/restore and counter/comparison instructions execute.',
      'compared':['entire512byteSH4context','cycle counter and original pairing metadata','ordered fetch/data events includingPC/cycles','full sparse prepared memory and writes','exceptions/Stop and no speculative unmatched-successor fetches'],'inputPins':pins,'fixtureSourceSHA256':sha(src),'fixtureLibrarySHA256':sha(lib),'wordIncludeSHA256':sha(out/'chain_words.inc'),'originalDelayDispatchCopySHA256':sha(dsrc),
      'limits':'Deterministic synthetic stack/function-table data, not a game replay. Exact original instructions and actual compiled chain execute. Cycle exhaustion verifies return to the unchanged scheduler boundary; outer IRQ delivery, full hardware/MMU behavior and real-time performance are not claimed. Mapping tests and whole-engine replay remain separately required.'}
    (out/'acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k]for k in ['passed','cases','caseGroups','failClosedCases','completeLoopProof']}))
if __name__=='__main__':main()
