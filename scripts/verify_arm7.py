#!/usr/bin/env python3
"""Compare fixed ARM operations against the pinned original instruction body.

The product scheduler/helper implementations are also checked by full-core hybrid
replays; this isolated fixture compares all registers, bus events and sound RAM.
"""
import argparse,json,re,subprocess,sys
from pathlib import Path
from build_arm7 import ROOT,SOURCE,REFERENCE,sha,save
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import compile_command
REFERENCE_WRAPPER=r'''
#include "arm7_fixed.h"
namespace aica::arm {
void fixture_original_step() {
 if(arm_Reg[INTR_PEND].I)CPUFiq();
 reg[15].I=armNextPC+8;
 int &clockTicks=arm7ClockTicks;
 #include "arm-new.h"
}
}
'''
HARNESS=r'''
#include "hw/arm7/arm7.h"
#include "hw/arm7/arm_mem.h"
#include "hw/aica/aica_if.h"
#include <cstdio>
#include <cstring>
#include <vector>
#include <stdexcept>
settings_t settings;
namespace aica { RamRegion aica_ram; void timeStep() {} }
void GenericLog(LogTypes::LOG_LEVELS,LogTypes::LOG_TYPE,const char*,int,const char*,...) {}
extern "C" [[noreturn]] void vt_fixed_fault(const char*,uint32_t,uint32_t,const char*) {throw std::runtime_error("guard");}
static std::vector<uint64_t> events;
namespace aica::arm {
 bool aica_interr,e68k_out;u32 aica_reg_L,e68k_reg_L,e68k_reg_M;
 template<typename T>T readReg(u32 addr) {events.push_back((uint64_t(sizeof(T))<<56)|addr);return T((addr*0x49e67c6du)^0x146abb7f);}
 template<typename T>void writeReg(u32 addr,T value) {events.push_back((1ULL<<63)|(uint64_t(sizeof(T))<<56)|(uint64_t(addr)<<32)|uint32_t(value));}
 template u8 readReg<u8>(u32);template u16 readReg<u16>(u32);template u32 readReg<u32>(u32);
 template void writeReg<u8>(u32,u8);template void writeReg<u16>(u32,u16);template void writeReg<u32>(u32,u32);
 void vt_arm7_fixed_step();void fixture_original_step();
}
using namespace aica;using namespace aica::arm;
struct Context {reg_pair regs[RN_ARM_REG_COUNT];int clock,mode;bool irq,fiq,enabled,interr,out;u32 al,el,em;};
static Context get(){Context c{};std::memcpy(c.regs,arm_Reg,sizeof(arm_Reg));c.clock=arm7ClockTicks;c.mode=armMode;c.irq=armIrqEnable;c.fiq=armFiqEnable;c.enabled=Arm7Enabled;c.interr=aica_interr;c.out=e68k_out;c.al=aica_reg_L;c.el=e68k_reg_L;c.em=e68k_reg_M;return c;}
static void put(const Context &c){std::memcpy(arm_Reg,c.regs,sizeof(arm_Reg));arm7ClockTicks=c.clock;armMode=c.mode;armIrqEnable=c.irq;armFiqEnable=c.fiq;Arm7Enabled=c.enabled;aica_interr=c.interr;e68k_out=c.out;aica_reg_L=c.al;e68k_reg_L=c.el;e68k_reg_M=c.em;}
static uint32_t rng=0x94978fad;
static uint32_t randomWord(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
struct Entry{uint32_t pc,word;};
static const Entry entries[]={ ENTRIES };
int main(){
 settings.platform.aram_size=8*1024*1024;settings.platform.aram_mask=settings.platform.aram_size-1;
 std::vector<uint8_t> ram(ARAM_SIZE),before(ARAM_SIZE),expectedRAM(ARAM_SIZE);aica_ram.setRegion(ram.data(),ram.size());
 for(auto &b:before)b=randomWord();
 unsigned count=0,negative=0;const int modes[]={0x10,0x11,0x12,0x13,0x17,0x1b,0x1f};
 for(const auto entry:entries)for(int seed=0;seed<(entry.pc==0x1c ? 4:2);++seed){
  Context initial{};
  for(auto &r:initial.regs)r.I=randomWord()&0xffffff;
  initial.mode=modes[count%7];initial.irq=seed;initial.fiq=!seed;initial.enabled=true;initial.clock=-int(randomWord()%50);
  initial.regs[RN_CPSR].I=initial.mode|(randomWord()&0xf00000c0);initial.regs[RN_SPSR].I=0x10|(randomWord()&0xf00000c0);
  initial.regs[RN_PSR_FLAGS].I=randomWord()&0xf0000000;initial.regs[R15_ARM_NEXT].I=entry.pc;initial.regs[INTR_PEND].I=(seed>=2);
  std::memcpy(before.data()+(entry.pc&ARAM_MASK),&entry.word,4);
  put(initial);ram=before;events.clear();fixture_original_step();
  const Context expectedContext=get();const auto expectedEvents=events;expectedRAM=ram;
  put(initial);ram=before;events.clear();if(arm_Reg[INTR_PEND].I)CPUFiq();arm_Reg[15].I=arm_Reg[R15_ARM_NEXT].I+8;vt_arm7_fixed_step();const Context actual=get();
  if(std::memcmp(&actual,&expectedContext,sizeof(actual))||events!=expectedEvents||ram!=expectedRAM){
   std::fprintf(stderr,"ARM mismatch pc %08x word %08x seed %d\n",entry.pc,entry.word,seed);
   for(unsigned i=0;i<RN_ARM_REG_COUNT;++i)if(actual.regs[i].I!=expectedContext.regs[i].I)std::fprintf(stderr,"R%u %08x != %08x\n",i,actual.regs[i].I,expectedContext.regs[i].I);
   return 1;
  }
  ++count;
 }
 for(uint32_t pc:{0xfffffffdu,0x001fffffu,0x77777770u}){
  arm_Reg[R15_ARM_NEXT].I=pc;try{vt_arm7_fixed_step();return 2;}catch(const std::runtime_error&){++negative;}
 }
 const auto entry=entries[0];uint32_t changed=0xdeabcdef;std::memcpy(ram.data()+(entry.pc&ARAM_MASK),&changed,4);arm_Reg[R15_ARM_NEXT].I=entry.pc;
 try{vt_arm7_fixed_step();return 3;}catch(const std::runtime_error&){++negative;}
 std::printf("{\"fullStateCases\":%u,\"unknownPCOrWordGuards\":%u}\n",count,negative);
}
'''
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--manifest',type=Path,default=ROOT/'build/native/cpu/arm7/manifest.json');p.add_argument('--generated',type=Path,default=ROOT/'build/generated/arm7');a=p.parse_args();out=a.output.resolve()
 if out.exists():p.error('Use a fresh output directory')
 out.mkdir(parents=True);(out/'executed.py').write_bytes(Path(__file__).read_bytes())
 manifest=json.loads(a.manifest.read_text());pins={Path(k) if Path(k).is_absolute() else ROOT/k:v for k,v in manifest['objects'].items()}
 for f,h in pins.items():
  if sha(f)!=h:raise RuntimeError('Stale ARM7 object '+str(f))
 dispatch=(a.generated/'arm7_dispatch.cpp').read_text()
 entries=re.findall(r'\{(0x[0-9a-f]+)u,(0x[0-9a-f]+)u,vt_arm7_op_',dispatch)
 if not entries:raise RuntimeError('No fixed entries')
 source=out/'original.cpp';source.write_text(REFERENCE_WRAPPER)
 harness=out/'fixture.cpp';harness.write_text(HARNESS.replace('ENTRIES',','.join('{'+pc+'u,'+word+'u}' for pc,word in entries)))
 objects=[]
 for f in (source,harness):
  obj=f.with_suffix('.o');cmd,_=compile_command('core/hw/arm7/arm7.cpp',f,obj);cmd[1:1]=['-I'+str(a.generated.resolve())]
  with f.with_suffix('.log').open('w') as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
  objects.append(obj)
 binary=out/'fixture'
 subprocess.run(['clang++','-std=c++17','-arch','arm64','-mmacosx-version-min=14.0',*[str(f) for f in objects],*[str(f) for f in pins],'-o',str(binary)],check=True)
 result=json.loads(subprocess.check_output([binary],text=True));result.update({'status':'passed','scriptSHA256':sha(__file__),'originalInstructionBodySHA256':sha(SOURCE/'core/hw/arm7/arm-new.h'),'manifestSHA256':sha(a.manifest),'fixtureBinarySHA256':sha(binary),'fixedIdentities':len(entries),'productionObjectHashes':{str(f.relative_to(ROOT)):h for f,h in pins.items()},'comparison':'All 50 registers, flags, mode and interrupt latches, cycles, exact memory-mapped bus events and entire 8MiB sound RAM. Two seeded states per fixed PC/word binding, plus FIQ-entry cases at each admitted vector image.'})
 save(out/'acceptance.json',result);print(json.dumps({k:v for k,v in result.items() if k in ('status','fullStateCases','samples','unknownPCOrWordGuards','unknownProgramGuards','testedProgramImages','fixedIdentities')}))
if __name__=='__main__':main()
