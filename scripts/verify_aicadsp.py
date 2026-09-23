#!/usr/bin/env python3
"""Isolated full-state/RAM AICA DSP fixed-step comparison with pinned original."""
import argparse,hashlib,json,struct,subprocess,sys
from pathlib import Path
from build_arm7 import ROOT,SOURCE,REFERENCE,sha,save
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import compile_command
HARNESS=r'''
#include "hw/aica/dsp.h"
#include "hw/aica/aica.h"
#include "hw/aica/aica_if.h"
#include <cstdio>
#include <fstream>
#include <vector>
#include <stdexcept>
#include <cstring>
settings_t settings;
namespace aica { RamRegion aica_ram; static DSPData_struct data; DSPData_struct *const DSPData=&data; }
namespace aica::dsp { void originalRunStep(); }
extern "C" [[noreturn]] void vt_fixed_fault(const char*,uint32_t,uint32_t,const char*) {throw std::runtime_error("guard");}
static uint32_t rng=0x784fad19;
static uint32_t randomWord(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
using namespace aica; using namespace aica::dsp;
int main(int argc,char **argv) {
 settings.platform.aram_size=8*1024*1024;settings.platform.aram_mask=settings.platform.aram_size-1;
 std::vector<uint8_t> ram(ARAM_SIZE), before(ARAM_SIZE), expectedRAM(ARAM_SIZE);
 aica_ram.setRegion(ram.data(),ram.size());
 for(auto &b:before)b=randomWord();
 unsigned cases=0,negative=0;
 for(int file=1;file<argc;++file) {
  uint32_t program[512];std::ifstream input(argv[file],std::ios::binary);input.read((char*)program,sizeof(program));
  if(input.gcount()!=sizeof(program))return 2;
  for(int seed=0;seed<4;++seed) {
   DSPState initial{};DSPData_struct registers{};
   for(auto &v:initial.TEMP)v=int32_t(randomWord()<<8)>>8;
   for(auto &v:initial.MEMS)v=int32_t(randomWord()<<8)>>8;
   for(auto &v:initial.MIXS)v=int32_t(randomWord()<<12)>>12;
   initial.RBL=(1u<<(13+(seed%4)))-1;initial.RBP=randomWord()&0x7ffffe;
   initial.MDEC_CT=1+randomWord()%initial.RBL;
   for(auto &v:registers.COEF)v=randomWord()&65535;
   for(auto &v:registers.MADRS)v=randomWord()&65535;
   for(auto &v:registers.EXTS)v=randomWord()&65535;
   for(auto &v:registers.EFREG)v=randomWord();
   std::memcpy(registers.MPRO,program,sizeof(program));
   state=initial;*DSPData=registers;ram=before;
   for(int sample=0;sample<4;++sample)originalRunStep();
   const DSPState expectedState=state;const DSPData_struct expectedRegisters=*DSPData;expectedRAM=ram;
   state=initial;*DSPData=registers;ram=before;
   for(int sample=0;sample<4;++sample)runStep();
   if(std::memcmp(&state,&expectedState,sizeof(state)) || std::memcmp(DSPData,&expectedRegisters,sizeof(*DSPData)) || ram!=expectedRAM) {
    std::fprintf(stderr,"DSP mismatch program %d seed %d\n",file,seed);return 1;
   }
   ++cases;
  }
 }
 // Original all-zero stopped state, reprogram selection and an unknown image.
 std::memset(DSPData->MPRO,0,sizeof(DSPData->MPRO));state.dirty=true;state.stopped=false;step();
 if(!state.stopped || state.dirty)return 3;
 for(unsigned index:{0u,17u,511u}) {
  std::memset(DSPData->MPRO,0,sizeof(DSPData->MPRO));DSPData->MPRO[index]=0xabcdef01u;state.dirty=true;
  try{step();return 4;}catch(const std::runtime_error&){++negative;}
 }
 std::printf("{\"fullStateCases\":%u,\"samples\":%u,\"unknownProgramGuards\":%u,\"zeroProgramStopped\":true}\n",cases,cases*4,negative);
}
'''
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trace',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,required=True);p.add_argument('--manifest',type=Path,default=ROOT/'build/native/cpu/aicadsp/manifest.json');a=p.parse_args()
 out=a.output.resolve()
 if out.exists():p.error('Use a fresh output directory')
 out.mkdir(parents=True);(out/'executed.py').write_bytes(Path(__file__).read_bytes())
 manifest=json.loads(a.manifest.read_text());pins={Path(k) if Path(k).is_absolute() else ROOT/k:v for k,v in manifest['objects'].items()}
 for f,h in pins.items():
  if sha(f)!=h:raise RuntimeError('Stale DSP object '+str(f))
 original=SOURCE/'core/hw/aica/dsp_interp.cpp'
 source=out/'original.cpp';source.write_text(original.read_text().replace('void runStep()','void originalRunStep()'))
 harness=out/'fixture.cpp';harness.write_text(HARNESS)
 objects=[]
 for f in (source,harness):
  obj=f.with_suffix('.o');cmd,_=compile_command('core/hw/aica/dsp_interp.cpp',f,obj)
  with f.with_suffix('.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
  objects.append(obj)
 binary=out/'fixture'
 subprocess.run(['clang++','-std=c++17','-arch','arm64','-mmacosx-version-min=14.0',*[str(f) for f in objects],*[str(f) for f in pins],'-o',str(binary)],check=True)
 recipe=json.loads((ROOT/'Configuration/aicadsp-program.json').read_text());media=ROOT/'build/assets/vtennis'/recipe['name']
 if sha(media)!=recipe['fileSHA256']:raise RuntimeError('Changed fixture DSP media')
 program=media.read_bytes()[recipe['offset']:recipe['offset']+recipe['bytes']]
 if hashlib.sha256(program).hexdigest()!=recipe['programSHA256']:raise RuntimeError('Changed fixture DSP program')
 raw_images=set()
 for index in range(513):
  raw_images.add(program[:index*4]+bytes((512-index)*4))
  raw_images.add(bytes(index*4)+program[index*4:])
 images={};(out/'programs').mkdir()
 for index,raw in enumerate(sorted(raw_images)):
  f=out/'programs'/f'{index:04d}.bin';f.write_bytes(raw);images[sha(f)]=f
 for trace in a.trace:
  for row in (json.loads(x) for x in trace.read_text().splitlines() if x):
   if row['kind']=='program':
    f=Path(str(trace)+'.data')/row['file']
    if sha(f) not in images:raise RuntimeError('Observed DSP program outside fixed fixture closure')
 output=subprocess.check_output([binary,*images.values()],text=True)
 result=json.loads(output);result.update({'status':'passed','scriptSHA256':sha(__file__),'originalSourceSHA256':sha(original),'manifestSHA256':sha(a.manifest),'fixtureBinarySHA256':sha(binary),'testedProgramImages':len(images),'programSHA256s':sorted(images),'productionObjectHashes':{str(f.relative_to(ROOT)):h for f,h in pins.items()},'comparison':'All DSP state fields, entire DSP register block, all 8MiB sound RAM; four consecutive samples per seeded case at product optimization'})
 save(out/'acceptance.json',result);print(json.dumps({k:v for k,v in result.items() if k in ('status','fullStateCases','samples','unknownPCOrWordGuards','unknownProgramGuards','testedProgramImages','fixedIdentities')}))
if __name__=='__main__':main()
