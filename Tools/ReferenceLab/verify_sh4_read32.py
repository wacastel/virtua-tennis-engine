#!/usr/bin/env python3
"""Test the actual mapped data-read32 candidate against original address-space semantics."""
from pathlib import Path
import argparse,ctypes,hashlib,json,re,shutil,struct,subprocess,sys
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
    pins.update({rel(original):sha(original),rel(original_object):sha(original_object),rel(Path(__file__)):sha(__file__),rel(Path(__file__).with_name('sh4_read32_fixture.cpp')):sha(Path(__file__).with_name('sh4_read32_fixture.cpp'))})
    shutil.copy2(Path(__file__),out/'verifier-executed.py');source=out/'fixture.cpp';shutil.copy2(Path(__file__).with_name('sh4_read32_fixture.cpp'),source)
    obj=out/'fixture.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',source,obj)
    cmd[1:1]=['-I'+str(candidate),'-I'+str(ROOT/'build/generated/sh4')]
    with (out/'compile.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_read32_fixture_case\n')
    compile_cmd=list(cmd)
    library=out/'fixture.dylib';cmd=link_command({mapping['originalObjectTarget']:candidate/mapping['replacementObject']},library)
    cmd=[('-Wl,-exported_symbols_list,'+str(exports))if x.startswith('-Wl,-exported_symbols_list,')else x for x in cmd]+[str(obj)]
    with (out/'link.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    link_cmd=list(cmd)
    disassembly=subprocess.check_output(['xcrun','otool','-tvV',str(original_object)],text=True)
    marker='__ZN9addrspace6read32Ej:'
    if marker not in disassembly:raise RuntimeError('Original read32 symbol absent')
    read32_disassembly=disassembly.split(marker,1)[1].split('__ZN9addrspace6read64Ej:',1)[0]
    if not re.search(r'lsr\s+w\d+, w\d+, w\d+',read32_disassembly) or not re.search(r'ldr\s+w0, \[x\d+, x\d+\]',read32_disassembly):raise RuntimeError('Original mapped read32 shift/load machine semantics differ')
    (out/'original-read32-arm64.txt').write_text(read32_disassembly)
    dll=ctypes.CDLL(str(library));fn=dll.vt_read32_fixture_case;fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p,ctypes.c_uint];fn.restype=ctypes.c_int
    output=ctypes.create_string_buffer(16384);counts={};read_steps=0;expectations=0
    def run(params,implementation):
        p=list(params);p.insert(5,implementation);values=(ctypes.c_uint32*8)(*p);n=fn(values,output,len(output))
        if n<=0:raise RuntimeError('Fixture failed: '+str(n)+' '+str(params))
        return ctypes.string_at(output,n)
    def compare(category,params):
        nonlocal read_steps,expectations
        before=run(params,0);after=run(params,1)
        if before!=after:
            (out/'mismatch-original.bin').write_bytes(before);(out/'mismatch-fixed.bin').write_bytes(after)
            (out/'mismatch.json').write_text(json.dumps({'parameters':params,'category':category,'firstByte':next((i for i,(a,b)in enumerate(zip(before,after))if a!=b),None)},indent=2)+'\n')
            raise RuntimeError('Mapped read32 mismatch: '+category+' '+str(params))
        counts[category]=counts.get(category,0)+1
        count=struct.unpack_from('<I',before)[0];rows=[struct.unpack_from('<8I',before,4+32*i)for i in range(count)];read_steps+=count
        scenario,page,bits,offset,effect,value_a,handler_id=params
        address=(page<<24)|(offset&0xffffff);value_b=value_a^0x6da573c1
        if scenario in [0,5]:
            if any(row[:2]!=(value_a,0)or row[4:7]!=(0x8c123456,100,0)for row in rows):raise RuntimeError('Known mapped raw32 value, unchanged PC/cycles or no-device-call expectation failed')
            expectations+=count
        if not effect:
            for row in rows:
                if row[1:6]!=(0,0,0,0x8c123456,100):raise RuntimeError('Unexpected mapped data exception/PC/cycle change')
                expectations+=1
            hv=(0x79b3d120^(address>>1)^1)&0xffffffff;av=(0xb1f08241^(address>>1)^1)&0xffffffff
            if scenario==4:
                if [r[0]for r in rows]!=[value_a,value_b,hv,value_a]or[ r[6]for r in rows]!=[0,0,1,1]:raise RuntimeError('Live mapping changes not observed')
                expectations+=4
            if scenario==3:
                if [r[0]for r in rows]!=[hv,value_b,value_b,value_b]or any(r[6]!=1 for r in rows):raise RuntimeError('Handler-to-mapped transition differs')
                expectations+=4
            if scenario==2:
                if rows[0][0]!=av or rows[0][6]!=1:raise RuntimeError('Alternate reader was bypassed despite a mapped page')
                expectations+=1
            if scenario==6:
                expected=[hv,*[(0xb1f08241^(address>>1)^i)&0xffffffff for i in [2,3,4]]]
                if [r[0]for r in rows]!=expected or any(r[7]!=1 for r in rows):raise RuntimeError('Handler reader changes not observed')
                expectations+=4
            if scenario==7:
                if [r[0]for r in rows]!=[av,value_a,value_a,value_a]or any(r[7]!=0 for r in rows):raise RuntimeError('Alternate reader changes not observed')
                expectations+=4
        if scenario in [1,2] and effect&(4|8):
            ending=1 if effect&4 else 2
            if rows[0][1]!=ending or rows[0][6]!=1:raise RuntimeError('Device exception did not preserve one callback')
            expectations+=1
    patterns=[0,0xffffffff,0x80000000,0x7fffffff,0x3f800000,0xbf800000,0x7f800000,0xff800000,0x7fc01234,0x7f801234,0x00000001,0x80000001]
    for page in range(256):
        for bits in range(1,27):
            mask=(1<<bits)-1
            for offset in sorted({0,1,2,3,mask&0xffffff,(mask-1)&0xffffff,(mask-2)&0xffffff,0xfffffc,0xfffffd,0xfffffe,0xffffff}):
                compare('mapped_pages_masks_alignments_boundaries',[0,page,bits,offset,0,patterns[(page+bits+offset)%len(patterns)],1])
    for bits in range(27,33):
        for page in range(4):
            for offset in [0,1,2,3,0xfffffc,0xfffffd,0xfffffe,0xffffff]:compare('large_masks_and_zero_shift',[0,page,bits,offset,0,0x5a96c31f,1])
    for value in patterns:
        for page in [0,12,140,255]:
            for offset in [0,1,2,3,0x1ffffd,0x1ffffe,0x1fffff]:compare('exact_integer_and_float_payload_bits',[0,page,21,offset,0,value,1])
    for page in [0,1,12,44,128,140,172,255]:
        for bits in [1,12,21,24,26]:
            for offset in [0,1,2,3,0xfffffd,0xfffffe,0xffffff]:
                for scenario in [3,4,5,6,7]:compare('live_remaps_mirrors_reader_switches',[scenario,page,bits,offset,0,0x83f51209,1])
    for page in [0,12,140,255]:
        for handler_id in [1,15,31]:
            for scenario in [1,2,3,6,7]:
                for effect in [0,1,2,3,4,5,6,8,9,10,16,17,18,19,20,24]:
                    for offset in [0,1,2,3,0xfffffd,0xfffffe,0xffffff]:compare('device_alternate_side_effects_order_and_exceptions',[scenario,page,21,offset,effect,0x7f801234,handler_id])
    for path,digest in pins.items():
        if sha(ROOT/path)!=digest:raise RuntimeError('Input changed during validation: '+path)
    report={'passed':True,'inputMode':cm['identityKind'],'cases':sum(counts.values()),'readSteps':read_steps,'independentValueAndStateAssertions':expectations,'caseGroups':counts,
      'compared':['actual candidate vt_mapped_read32 against original addrspace::read32 and captured alternate readers','exact returned u32 bits including IEEE754 zeros/subnormal/infinities/NaN payloads without numeric conversion','full prepared SH4 context, unchanged data-read PC and cycle state except explicit device effects','ordered handler calls, addresses, observed context and side effects','exceptions and debugger Stop boundaries','active reader and mapping changes after callbacks','prepared buffer bytes including contiguous unaligned loads crossing original mask/page boundaries'],
      'sourceExposure':'Only original table linkage changes from static to hidden; inverse transformation equals pinned original source. Original read32 and private RF32 unchanged.',
      'originalARM64Semantics':'Pinned original read32 object uses a 32-bit variable LSR (low five shift bits) and one LDR W load. The actual helper is compared to that unchanged compiled body for shifts31..0 and all byte alignments.',
      'inputPins':pins,'fixtureSourceSHA256':sha(source),'fixtureLibrarySHA256':sha(library),'compileCommand':compile_cmd,'linkCommand':link_cmd,'originalRead32DisassemblySHA256':sha(out/'original-read32-arm64.txt'),
      'limits':'Isolated deterministic original mapping API tests. Full MMU/cache/device emulation and performance are not claimed. Mapping masks cover shifts31 through0; larger masks use addresses within allocated64MiB backing plus boundary padding. Actual CPU block/chain suites and complete gameplay replay remain independently required.'}
    (out/'acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k]for k in ['passed','cases','readSteps','independentValueAndStateAssertions','caseGroups']}))
if __name__=='__main__':main()
