#!/usr/bin/env python3
"""Test the actual mapped-fetch candidate against original address-space semantics."""
from pathlib import Path
import argparse,ctypes,hashlib,json,shutil,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
from build_observer import SOURCE,REFERENCE,compile_command,link_command
from sh4_fixture_inputs import add_fixture_arguments,fixture_inputs

def sha(path):
    with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def rel(path):return str(Path(path).resolve().relative_to(ROOT))
def main():
    ap=argparse.ArgumentParser(description=__doc__);add_fixture_arguments(ap,ROOT)
    args=ap.parse_args();inputs=fixture_inputs(ROOT,args);candidate=inputs.include_directory;out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest=inputs.cpu_manifest if args.candidate is None else candidate/'manifest.json';cm=inputs.normalized;mapping=cm['mappedFetch'];pins=dict(inputs.input_pins)
    for group in ['sources','objects']:
        for name,digest in cm[group].items():
            p=candidate/name
            if sha(p)!=digest:raise RuntimeError('Candidate changed: '+name)
            pins[rel(p)]=digest
    original=ROOT/mapping['originalSource'];original_text=original.read_text();derived=inputs.derived_addrspace_source
    old='static void* memInfo_ptr[0x100];';new='__attribute__((visibility("hidden"))) void* memInfo_ptr[0x100];'
    if original_text.count(old)!=1 or derived.read_text().replace(new,old,1)!=original_text:raise RuntimeError('Address-space exposure changes more than table linkage')
    if sha(original)!=mapping['originalSourceSHA256']:raise RuntimeError('Original source identity mismatch')
    original_object=REFERENCE/mapping['originalObjectTarget']
    if sha(original_object)!=mapping['originalObjectSHA256']:raise RuntimeError('Original object identity mismatch')
    pins.update({rel(original):sha(original),rel(original_object):sha(original_object),rel(Path(__file__)):sha(__file__),rel(Path(__file__).with_name('sh4_fetch_fixture.cpp')):sha(Path(__file__).with_name('sh4_fetch_fixture.cpp'))})
    shutil.copy2(Path(__file__),out/'verifier-executed.py');source=out/'fixture.cpp';shutil.copy2(Path(__file__).with_name('sh4_fetch_fixture.cpp'),source)
    obj=out/'fixture.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',source,obj)
    cmd[1:1]=['-I'+str(candidate),'-I'+str(ROOT/'build/generated/sh4')]
    with (out/'compile.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_fetch_fixture_case\n')
    library=out/'fixture.dylib';cmd=link_command({mapping['originalObjectTarget']:candidate/mapping['replacementObject']},library)
    cmd=[('-Wl,-exported_symbols_list,'+str(exports))if x.startswith('-Wl,-exported_symbols_list,')else x for x in cmd]+[str(obj)]
    with (out/'link.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(library));fn=dll.vt_fetch_fixture_case;fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p,ctypes.c_uint];fn.restype=ctypes.c_int
    output=ctypes.create_string_buffer(16384);counts={};read_steps=0;expectations=0
    def run(params,implementation):
        p=list(params);p.insert(7,implementation);values=(ctypes.c_uint32*10)(*p);n=fn(values,output,len(output))
        if n<=0:raise RuntimeError('Fixture failed: '+str(n))
        return ctypes.string_at(output,n)
    def compare(category,params):
        nonlocal read_steps,expectations
        before=run(params,0);after=run(params,1)
        if before!=after:
            (out/'mismatch-original.bin').write_bytes(before);(out/'mismatch-fixed.bin').write_bytes(after)
            (out/'mismatch.json').write_text(json.dumps({'parameters':params,'category':category,'firstByte':next((i for i,(a,b)in enumerate(zip(before,after))if a!=b),None)},indent=2)+'\n')
            raise RuntimeError('Mapped fetch mismatch: '+category+' '+str(params))
        counts[category]=counts.get(category,0)+1
        count=struct.unpack_from('<I',before)[0];rows=[struct.unpack_from('<8I',before,4+32*i)for i in range(count)];read_steps+=count
        scenario,page,bits,offset,effect,mmu,wrapper,seed,handler_id=params
        address=(page<<24)|(offset&0xffffff);value_a=(0xc351^(seed*0x311))&65535;value_b=value_a^0x6da5
        if wrapper and address&1 and not mmu:
            if any(row[1:5]!=(1,address,0xe0,address)or row[6]!=0 for row in rows):raise RuntimeError('Odd-PC rejection or no-side-effect expectation failed')
            expectations+=count;return
        if scenario in [0,5]:
            for row in rows:
                if row[:2]!=(value_a,0)or row[4]!=(address+2*wrapper)&0xffffffff or row[6]!=0:raise RuntimeError('Known mapped word or PC-advance expectation failed')
                expectations+=1
        if scenario==4 and not effect:
            expected=[value_a,value_b,(0x7900^(address>>1)^1)&65535,value_a]
            if [row[0]for row in rows]!=expected:raise RuntimeError('Live remap sequence differs')
            expectations+=4
        if scenario==3 and not effect:
            if [row[0]for row in rows]!=[(0x7900^(address>>1)^1)&65535,value_b,value_b,value_b]or any(row[6]!=1 for row in rows):raise RuntimeError('Handler-to-mapped transition differs')
            expectations+=4
        if scenario==2 and not effect:
            if rows[0][0]!=(0xb100^(address>>1)^1)&65535 or rows[0][6]!=1:raise RuntimeError('Alternate reader did not override mapped table')
            expectations+=1
        if scenario==7 and not effect:
            if [row[0]for row in rows]!=[(0xb100^(address>>1)^1)&65535,value_a,value_a,value_a]or any(row[7]!=0 for row in rows):raise RuntimeError('Reader switch not observed on the next fetch')
            expectations+=4
    for page in range(256):
        for bits in range(1,27):
            mask=(1<<bits)-1
            for offset in sorted({0,1,2,mask&0xffffff,(mask-1)&0xffffff,0xfffffe,0xffffff}):
                compare('mapped_pages_masks_boundaries',[0,page,bits,offset,0,0,0,(page+bits)&255,1])
        for offset in [0,1,0xfffffe,0xffffff]:
            for mmu in [0,1]:compare('mapped_instruction_PC_and_alignment',[0,page,21,offset,0,mmu,1,page,1])
    for bits in range(27,33):
        for page in range(4):
            for offset in [0,1,0xfffffe,0xffffff]:compare('large_masks_and_zero_shift',[0,page,bits,offset,0,0,0,bits,1])
    for page in [0,1,12,44,128,140,172,255]:
        for bits in [1,12,21,24,26]:
            for offset in [0,2,0xfffffe]:
                for scenario in [3,4,5,6,7]:
                    for wrapper in [0,1]:compare('live_remaps_mirrors_reader_switches',[scenario,page,bits,offset,0,0,wrapper,19,1])
    for page in [0,12,140,255]:
        for handler_id in [1,15,31]:
            for scenario in [1,2,3,6,7]:
                for effect in [0,1,2,3,4,5,6,8,9,10]:
                    for wrapper in [0,1]:
                        for offset in [0,1,0xfffffe]:compare('handler_alternate_side_effects_and_exceptions',[scenario,page,21,offset,effect,0,wrapper,23,handler_id])
    for path,digest in pins.items():
        if sha(ROOT/path)!=digest:raise RuntimeError('Input changed during validation: '+path)
    report={'passed':True,'inputMode':cm['identityKind'],'cases':sum(counts.values()),'readSteps':read_steps,'independentValueAndPCAssertions':expectations,'caseGroups':counts,
      'compared':['actual candidate vt_fetch_read16 and vt_mapped_fetch against original reader and ReadNexOp','returned words and full prepared SH4 context','ordered handler calls, observed PC and cycle state','exceptions and debugger Stop boundaries','active reader identity after callbacks'],
      'sourceExposure':'Only original table linkage changes from static to hidden; inverse transformation equals pinned original source. Original read16 and private RF16 table unchanged.',
      'inputPins':pins,'fixtureSourceSHA256':sha(source),'fixtureLibrarySHA256':sha(library),'compileCommand':cmd,
      'limits':'Isolated deterministic original mapping API tests, not complete MMU/cache/device validation or performance evidence. Valid power-of-two mapping masks cover shifts31 through0; larger masks use addresses within the allocated64MiB backing. The real CPU block suite and complete gameplay replay remain independently required.'}
    report['compileCommand']=None
    (out/'acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k]for k in ['passed','cases','readSteps','independentValueAndPCAssertions','caseGroups']}))
if __name__=='__main__':main()
