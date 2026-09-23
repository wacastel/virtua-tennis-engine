#!/usr/bin/env python3
"""Exercise actual constant-index ARM dispatcher object with isolated operation markers."""
from pathlib import Path
import argparse,bisect,hashlib,json,re,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import SOURCE,REFERENCE,compile_command

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rel(p):return str(Path(p).resolve().relative_to(ROOT))
PREVIOUS_SEARCH='void vt_arm7_fixed_step() {\n const u32 pc=arm_Reg[R15_ARM_NEXT].I;\n const FixedEntry *entry=std::lower_bound(std::begin(entries),std::end(entries),pc,\n   [](const FixedEntry &entry,u32 value){return entry.pc<value;});\n u32 actual=0;\n if (!(pc&3) && pc<ARAM_SIZE) {\n  actual=*(const u32*)&aica_ram[pc];\n  while(entry!=std::end(entries) && entry->pc==pc) {\n   if(entry->word==actual) { entry->operation(); return; }\n   ++entry;\n  }\n }\n vt_fixed_fault("AICA ARM7",pc,actual,"Untranslated PC or changed instruction word");\n}'
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--manifest',type=Path,default=ROOT/'build/native/cpu/arm7/manifest.json');ap.add_argument('--generated',type=Path,default=ROOT/'build/generated/arm7');ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);manifest=args.manifest.resolve();generated=args.generated.resolve();m=json.loads(manifest.read_text());generation=generated/'generation.json';g=json.loads(generation.read_text())
 pins={rel(manifest):sha(manifest),rel(generation):sha(generation),rel(__file__):sha(__file__)}
 if sha(generation)!=m['generationSHA256']:raise RuntimeError('Changed ARM generation manifest')
 for name,digest in {**m['sourcePins'],**m['objects']}.items():
  path=ROOT/name
  if sha(path)!=digest:raise RuntimeError('Stale sound input: '+name)
  pins[rel(path)]=digest
 for name,digest in g['sources'].items():
  if sha(generated/name)!=digest:raise RuntimeError('Changed generated source: '+name)
 fixed=generated/'arm7_dispatch.cpp';new=fixed.read_text()
 entries=[(int(p,16),int(w,16))for p,w in re.findall(r'\{(0x[0-9a-f]+)u,(0x[0-9a-f]+)u,vt_arm7_op_',new)]
 recipe_path=ROOT/'Configuration/arm7-programs.json';recipe=json.loads(recipe_path.read_text());expected=set();pins[rel(recipe_path)]=sha(recipe_path)
 for window in recipe['programWindows']:
  path=ROOT/'build/assets/vtennis'/window['name']
  if sha(path)!=window['fileSHA256']:raise RuntimeError('Changed sound media')
  data=path.read_bytes()[window['offset']:window['offset']+window['bytes']]
  if len(data)!=window['bytes'] or hashlib.sha256(data).hexdigest()!=window['windowSHA256']:raise RuntimeError('Changed ARM window')
  pins[rel(path)]=sha(path)
  for offset in range(0,len(data),4):expected.add((window['mappedBase']+offset,struct.unpack_from('<I',data,offset)[0]))
 if entries!=sorted(expected):raise RuntimeError('Ordered fixed bindings differ from authenticated media')
 buckets=[(int(a),int(b))for a,b in re.findall(r'\{([0-9]+)u,([0-9]+)u\}',new)]
 if len(buckets)!=8192 or {p for p,w in entries}!=set(range(0,32768,4)):raise RuntimeError('Unexpected slot domain')
 keys=[p for p,w in entries]
 for pc,(first,count)in zip(range(0,32768,4),buckets):
  start=bisect.bisect_left(keys,pc);end=bisect.bisect_right(keys,pc)
  if first!=start or count!=end-start:raise RuntimeError('Bucket differs from independently authenticated ordered search')
 binding_sha=hashlib.sha256(b''.join(struct.pack('<II',p,w)for p,w in entries)).hexdigest();bucket_sha=hashlib.sha256(b''.join(struct.pack('<HH',first,count)for first,count in buckets)).hexdigest()
 if g.get('dispatch',{}).get('orderedBindingSHA256')!=binding_sha or g['dispatch'].get('bucketSHA256')!=bucket_sha:raise RuntimeError('Generation dispatch identity differs')
 actual_objects=[ROOT/name for name in m['objects']if Path(name).name=='arm7_dispatch.o']
 if len(actual_objects)!=1:raise RuntimeError('Expected exactly one actual dispatcher object')
 actual_object=actual_objects[0]
 # Independent prior fixed-search algorithm, populated from authenticated media.
 old=new[:new.index('struct FixedBucket')]+PREVIOUS_SEARCH+'\n}\n'
 words=sorted({w for p,w in entries})
 header=r'''
#include "hw/arm7/arm7.h"
#include "hw/aica/aica_if.h"
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <stdexcept>
settings_t settings;
namespace aica { RamRegion aica_ram; }
struct Fault{};
struct Result {uint32_t calls=0,selected=0,pc=0,word=0;bool fault=false;std::string cpu,reason;bool operator==(const Result&b)const{return calls==b.calls&&selected==b.selected&&pc==b.pc&&word==b.word&&fault==b.fault&&cpu==b.cpu&&reason==b.reason;}};
static Result result;
extern "C" [[noreturn]] void vt_fixed_fault(const char*cpu,uint32_t pc,uint32_t word,const char*reason){result.fault=true;result.cpu=cpu;result.pc=pc;result.word=word;result.reason=reason;throw Fault();}
namespace aica::arm {
alignas(8) reg_pair arm_Reg[RN_ARM_REG_COUNT];
void vt_arm7_fixed_step();void vt_arm7_previous_step();
'''
 stubs='\n'.join(f'void vt_arm7_op_{w:08x}(){{++result.calls;result.selected=0x{w:08x}u;}}'for w in words)
 body=r'''
}
using namespace aica;using namespace aica::arm;
struct Entry{uint32_t pc,word;};static const Entry entries[]={ ENTRIES };
static std::vector<uint8_t> ram(8u<<20);static uint64_t comparisons=0,bindings=0,changed=0,holes=0,odd=0,bounds=0;
static Result run(bool fixed,uint32_t pc){
 for(unsigned i=0;i<RN_ARM_REG_COUNT;++i)arm_Reg[i].I=0x2958367u*(i+1);arm_Reg[R15_ARM_NEXT].I=pc;
 reg_pair before[RN_ARM_REG_COUNT];std::memcpy(before,arm_Reg,sizeof(before));result={};
 try{if(fixed)vt_arm7_fixed_step();else vt_arm7_previous_step();}catch(const Fault&){}
 if(std::memcmp(before,arm_Reg,sizeof(before)))throw std::runtime_error("dispatcher modified CPU state before operation marker");return result;
}
static void check(uint32_t pc,bool accepted,uint32_t word){
 auto original=run(false,pc),fixed=run(true,pc);if(!(original==fixed))throw std::runtime_error("original/indexed dispatch result mismatch");
 if(accepted){if(fixed.fault||fixed.calls!=1||fixed.selected!=word)throw std::runtime_error("wrong operation selected");}
 else {uint32_t actual=0;if(!(pc&3)&&pc<ARAM_SIZE)std::memcpy(&actual,ram.data()+pc,4);if(!fixed.fault||fixed.calls||fixed.pc!=pc||fixed.word!=actual||fixed.cpu!="AICA ARM7"||fixed.reason!="Untranslated PC or changed instruction word")throw std::runtime_error("wrong closed-guard/fault diagnostic");}
 ++comparisons;
}
int main(){
 settings.platform.aram_size=ram.size();settings.platform.aram_mask=ram.size()-1;aica_ram.setRegion(ram.data(),ram.size());
 for(uint32_t p=0;p<ram.size();p+=4){uint32_t v=p^0xcdfa314bu;std::memcpy(ram.data()+p,&v,4);}
 for(const auto e:entries){std::memcpy(ram.data()+e.pc,&e.word,4);check(e.pc,true,e.word);++bindings;}
 for(uint32_t p=0;p<32768;p+=4){uint32_t w=0xdeadc0de;for(;;++w){bool admitted=false;for(const auto e:entries)if(e.pc==p&&e.word==w){admitted=true;break;}if(!admitted)break;}std::memcpy(ram.data()+p,&w,4);check(p,false,0);++changed;for(unsigned low=1;low<=3;++low){check(p+low,false,0);++odd;}}
 for(uint32_t p=32768;p<ram.size();p+=4){check(p,false,0);++holes;}
 for(uint32_t p:{0x00800000u,0x00800001u,0x00800004u,0x80000000u,0xfffffffcu,0xffffffffu}){check(p,false,0);++bounds;}
 for(uint32_t size:{0u,4u,16u,32768u}){settings.platform.aram_size=size;settings.platform.aram_mask=size?size-1:0;for(const auto e:entries){std::memcpy(ram.data()+e.pc,&e.word,4);check(e.pc,e.pc<size,e.word);++bounds;}}
 std::printf("{\"comparisons\":%llu,\"authenticatedBindings\":%llu,\"changedWordGuards\":%llu,\"unalignedPCGuards\":%llu,\"unboundSoundRAMPCGuards\":%llu,\"ARAMBoundsAndSizeCases\":%llu}\n",comparisons,bindings,changed,odd,holes,bounds);
}
'''
 body=body.replace('ENTRIES',','.join('{'+hex(p)+','+hex(w)+'}'for p,w in entries))
 harness=out/'fixture.cpp';harness.write_text(header+stubs+body);previous=out/'previous.cpp';previous.write_text('#define vt_arm7_fixed_step vt_arm7_previous_step\n#define vt_fixed_arm7_marker vt_fixed_arm7_previous_marker\n'+old)
 pins[rel(fixed)]=sha(fixed);pins[rel(actual_object)]=sha(actual_object)
 (out/'executed.py').write_bytes(Path(__file__).read_bytes())
 objects=[]
 for src in [harness,previous]:
  obj=src.with_suffix('.o');cmd,_=compile_command('core/hw/arm7/arm7.cpp',src,obj);cmd[1:1]=['-I'+str(generated),'-I'+str(ROOT/'Sources/Bridge')]
  with src.with_suffix('.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
  objects.append(obj)
 binary=out/'fixture';subprocess.run(['clang++','-std=c++17','-arch','arm64','-mmacosx-version-min=14.0',*[str(x)for x in objects],str(actual_object),'-o',str(binary)],check=True)
 raw=subprocess.check_output([binary],text=True);r=json.loads(raw)
 for p,digest in pins.items():
  if sha(ROOT/p)!=digest:raise RuntimeError('Validation input changed: '+p)
 r.update(passed=True,orderedBindingSHA256=binding_sha,bucketSHA256=bucket_sha,bucketsComparedToPreviousOrderedSearch=len(buckets),inputPins=pins,fixtureSourceSHA256=sha(harness),fixtureBinarySHA256=sha(binary),limits='Dispatcher-only operation markers establish exact binding/guard and fault-argument selection. Full actual operation, CPU/FIQ/bus/8MiB RAM semantics are verified separately with31,800 cases. No performance or whole-engine claim.')
 (out/'acceptance.json').write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');print(raw)
if __name__=='__main__':main()
