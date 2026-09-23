// Development-only actual-main-RAM oracle for a guarded complete-loop contraction.
#include "types.h"
#define private public
#define protected public
#include "hw/sh4/sh4_interpreter.h"
#undef protected
#undef private
#include "hw/sh4/sh4_mem.h"
#include "hw/sh4/sh4_core.h"
#include "hw/sh4/sh4_interrupts.h"
#include "hw/sh4/sh4_mmr.h"
#include "hw/sh4/modules/mmu.h"
#include "cfg/option.h"
#include "network/ggpo.h"
#include "debug/gdb_server.h"
#include "run_dispatch.h"
#include "counter_loop.h"
#include <cstring>
#include <cstdlib>
#include <vector>
#include <algorithm>
#include <new>
#include <climits>
struct ChainWord {uint32_t pc;uint16_t word;};
#include "chain_words.inc"
namespace ggpo { extern bool inRollback; }
bool vt_fixture_fixed_delay=false;
extern "C" void vt_fixed_clear_error();
extern "C" const char *vt_fixed_error();
static constexpr uint32_t SIZE=32u<<20,START=0x0c0c183a,EXIT=0x0c0c1856,STACK=0x0c400100,COUNTER=0x0d500000,STOP=0x0d500100,TABLE=0x0c28a25c;
static uint8_t *ram,*baseline,*expected,*other;
static uint32_t mode,condition,effect,accesses;static Sh4Context *current;
struct Event {uint32_t kind,address,value,pc,cycles;};static std::vector<Event> events;
static uint32_t off(uint32_t addr){return addr&0x01ffffffu;}
static void put(uint32_t a,uint32_t v){std::memcpy(ram+off(a),&v,4);}static uint32_t get(uint32_t a){uint32_t v;std::memcpy(&v,ram+off(a),4);return v;}
static void touch(unsigned kind,uint32_t address,uint32_t value){
 events.push_back({kind,address,value,current->pc,uint32_t(current->cycle_counter)});++accesses;
 if(effect&1)current->cycle_counter-=2;
 if(accesses==3 && (effect&2))throw SH4ThrownException(current->pc-2,Sh4Ex_TlbMissRead);
 if(accesses==3 && (effect&4))throw debugger::Stop();
}
static u16 DYNACALL fetchAlt(u32 a){auto v=addrspace::read16(a);touch(0,a,v);return v;}
static u32 DYNACALL readAlt(u32 a){auto v=addrspace::read32(a);touch(1,a,v);return v;}
static void DYNACALL writeAlt(u32 a,u32 v){touch(2,a,v);addrspace::write32(a,v);}
static u32 DYNACALL deviceRead(u32 a){auto v=get(a);touch(1,a,v);return v;}
static void DYNACALL deviceWrite(u32 a,u32 v){touch(2,a,v);put(a,v);}
struct State {Sh4Context ctx;int last,mem,ratio;uint32_t ending,epc,event,irq,intevt;std::vector<Event> bus;};
static void alloc(){
 if(ram)return;
 for(auto p:{&ram,&baseline,&expected,&other})if(posix_memalign(reinterpret_cast<void**>(p),4096,SIZE))std::abort();
 std::memset(ram,0x5a,SIZE);std::memset(other,0x35,SIZE);
 if(posix_memalign(reinterpret_cast<void**>(&p_sh4rcb),64,sizeof(Sh4RCB)))std::abort();std::memset(p_sh4rcb,0,sizeof(Sh4RCB));
}
static void mappings(uint32_t alias){
 addrspace::init();mem_b.setRegion(ram,SIZE);settings.platform.system=DC_PLATFORM_NAOMI;settings.platform.ram_size=SIZE;settings.platform.ram_mask=SIZE-1;
 for(unsigned a:{0u,0x80u,0xa0u})addrspace::mapBlockMirror(ram,0x0c|a,0x0f|a,SIZE);
 IReadMem16=addrspace::read16;ReadMem8=addrspace::read8;ReadMem16=addrspace::read16;ReadMem32=addrspace::read32;ReadMem64=addrspace::read64;
 WriteMem8=addrspace::write8;WriteMem16=addrspace::write16;WriteMem32=addrspace::write32;WriteMem64=addrspace::write64;
 if(condition==1)mmuOn=true;
 if(condition==2)IReadMem16=fetchAlt;
 if(condition==3)ReadMem32=readAlt;
 if(condition==4)WriteMem32=writeAlt;
 if(condition==14)settings.platform.ram_size=16u<<20;
 if(condition==15)settings.platform.ram_mask=(16u<<20)-1;
 if(condition==16)addrspace::mapBlock(other,0x0d,0x0d,SIZE-1);
 if(condition==17)addrspace::mapBlock(ram,0x0d,0x0d,(16u<<20)-1);
 if(condition==18){auto h=addrspace::registerHandler(nullptr,nullptr,deviceRead,nullptr,nullptr,deviceWrite);addrspace::mapHandler(h,0x0d,0x0d);}
}
static void prepare(const uint32_t *a,Sh4Context&ctx){
 uint32_t seed=a[3]*0x9e3779b9u+0x1234567u;
 auto random=[&](){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;};
 std::memset(&ctx,0,sizeof(ctx));for(unsigned i=0;i<16;++i){ctx.r[i]=random();ctx.fr_hex(i)=random();ctx.xf[i]=float(int(random()%2000)-1000);}
 for(unsigned i=0;i<8;++i)ctx.r_bank[i]=random();ctx.mac.full=(uint64_t(random())<<32)|random();ctx.gbr=random();ctx.fpul=random();ctx.pr=random();ctx.vbr=0x0c600000;
 ctx.sr.setFull((random()&0x30000003u)|0x40000000u);ctx.old_sr.status=ctx.sr.status;ctx.fpscr.full=0;ctx.old_fpscr=ctx.fpscr;ctx.sr.IMASK=0;ctx.sr.BL=0;ctx.sr.RB=0;ctx.old_sr.status=ctx.sr.status;
 auto alias=a[4];ctx.pc=START|alias;ctx.r[13]=0x0c0bf320|alias;ctx.r[14]=0x1234;ctx.r[15]=STACK|alias;ctx.CpuRunning=1;ctx.cycle_counter=int(a[1]);ctx.sh4_sched_next=448*100;
 uint32_t c=COUNTER,d=STOP,s=STACK;
 if(condition==9)ctx.r[13]=0x0c0bf324|alias;
 if(condition==19)ctx.r[15]=s=STACK+1;
 if(condition==20)c=COUNTER+1;
 if(condition==21)d=STOP+1;
 if(condition==23)c=0x0c0bf320;
 if(condition==24)c=STACK-4;
 if(condition==25)c=STACK;
 if(condition==26)c=d;
 if(condition==27)c=TABLE+56;
 if(condition==28)c=TABLE+60;
 if(condition==29)ctx.r[15]=s=0x0c0c1858;
 if(condition==30)ctx.r[15]=s=STOP+4;
 if(condition==31)ctx.r[15]=s=TABLE+56+12;
 if(condition==33)d=0x005f0000;
 if(condition==34)c=0x005f0010;
 if(condition==35)ctx.r[15]=s=0x0c000008;
 if(condition==41)c=(STACK-4)|0x80000000u;
 if(condition==42)c=0x0e4000fcu;
 if(condition==43){c|=0x80000000u;d|=0xa0000000u;}
 if(condition==44){c=0x0f500000;d=0x0f500100;}
 for(const auto&w:chainWords)std::memcpy(ram+off(w.pc),&w.word,2);
 put(0x0c0bf360,TABLE);put(TABLE+56,0x0c0be080|alias);put(TABLE+60,condition==28?0x12345678:0x98761234);
 put(s,c);put(s+4,d);if(condition!=25&&condition!=27&&condition!=28&&condition!=23)put(c,a[2]);
 if(condition!=26)put(d,ctx.r[14]);else ctx.r[14]=a[2];
 if(condition==30)put(STOP,ctx.r[14]);
 if(condition==10)put(TABLE+56,0x0c0be084|alias);
 if(condition==11)put(d,ctx.r[14]+1);
 if(condition==32){const auto&w=chainWords[a[9]%(sizeof(chainWords)/sizeof(chainWords[0]))];ram[off(w.pc)]^=1;}
 if(condition==12)ctx.pc=START+2;
 if(condition==36)ctx.pc=START|0x40000000u;
 config::ThreadedRendering=condition==8;config::GGPOEnable=condition==5;config::NetworkEnable=condition==6;settings.network.online=condition==7;settings.naomi.multiboard=condition==37;settings.naomi.slave=condition==38;settings.naomi.drivingSimSlave=condition==39;ggpo::inRollback=condition==40;
 if(condition==45)std::memcpy(ram+(8u<<20),&ctx,sizeof(ctx));
}
static State execute(const uint32_t *a,const Sh4Context&prepared,bool native,uint32_t expectedIterations,uint32_t&hits,uint32_t&iterations,bool &unchanged){
 std::memcpy(ram,baseline,SIZE);Sh4cntx=prepared;auto&ctx=Sh4cntx;current=&ctx;events.clear();accesses=0;mmuOn=false;mappings(a[4]);
 interrupts_reset();if(a[10])SetInterruptPend(sh4_IRL_9);CCN_INTEVT=0;
 Sh4Interpreter original;original.ctx=&ctx;new(&original.sh4cycles)Sh4Cycles(a[7]);original.sh4cycles.init(&ctx);original.sh4cycles.lastUnit=static_cast<sh4_eu>(a[6]);original.sh4cycles.memOps=int(a[5]);
 Sh4Interpreter::Instance=&original;vt_fixture_fixed_delay=native;vt_fixed_clear_error();hits=iterations=0;
 State result{};uint32_t beforeCount=get(COUNTER);Sh4Context checkpoint;int beforeLast=original.sh4cycles.lastUnit,beforeMem=original.sh4cycles.memOps;std::vector<uint8_t>saved;
 try{
  const uint16_t first=original.ReadNexOp();
  if(mode==0||mode==2){
   Sh4Cycles *activeCycles=&original.sh4cycles;Sh4Context *activeContext=&ctx;
   if(condition==45){activeContext=reinterpret_cast<Sh4Context*>(ram+(8u<<20));activeContext->pc=ctx.pc;}
   if(condition==46){activeCycles=new(ram+(9u<<20))Sh4Cycles(a[7]);activeCycles->init(&ctx);activeCycles->lastUnit=static_cast<sh4_eu>(a[6]);activeCycles->memOps=int(a[5]);}
   checkpoint=ctx;saved.assign(ram,ram+SIZE);auto checkpointEvents=events.size();auto*oldContext=original.sh4cycles.ctx;
   if(condition==45)original.sh4cycles.ctx=activeContext;
   if(condition==13)original.sh4cycles.ctx=&checkpoint;
   if(native){hits=vt_try_counter_loop(activeContext,*activeCycles)?1:0;if(hits)iterations=beforeCount-get(COUNTER);}
   else if(expectedIterations){uint16_t w=first;unsigned loops=0,steps=0;do{original.ExecuteOpcode(w);if(ctx.pc==(START|a[4]))++loops;if(loops==expectedIterations)break;if(++steps>4096)throw FlycastException("fixture oracle loop bound");w=original.ReadNexOp();}while(true);}
   original.sh4cycles.ctx=oldContext;
   unchanged=std::memcmp(&ctx,&checkpoint,sizeof(ctx))==0&&std::memcmp(ram,saved.data(),SIZE)==0&&original.sh4cycles.lastUnit==beforeLast&&original.sh4cycles.memOps==beforeMem&&events.size()==checkpointEvents;
  }else{
   uint16_t word=first;unsigned instructions=0;
   do{
    if(native)vt_run_execute(&ctx,word,original.sh4cycles);else original.ExecuteOpcode(word);
    if((ctx.pc&0x1fffffffu)==EXIT){result.ending=1;break;}
    if(ctx.cycle_counter<=0)break;
    if(++instructions>32768){result.ending=5;break;}
    word=original.ReadNexOp();
   }while(true);
   if(mode==1 && ctx.cycle_counter<=0){ctx.cycle_counter+=SH4_TIMESLICE;result.irq=UpdateSystem_INTC();result.intevt=CCN_INTEVT;}
  }
 }catch(const SH4ThrownException&e){result.ending=2;result.epc=e.epc;result.event=e.expEvn;}catch(const debugger::Stop&){result.ending=3;}catch(const FlycastException&){result.ending=4;}
 result.ctx=ctx;result.last=original.sh4cycles.lastUnit;result.mem=original.sh4cycles.memOps;result.ratio=original.sh4cycles.cpuRatio;result.bus=events;
 Sh4Interpreter::Instance=nullptr;return result;
}
// mode,budget,counter,seed,alias,memOps,lastUnit,ratio,condition,aux,irq,expectedIterations
extern "C" int vt_counter_fixture_case(const uint32_t*a,uint32_t*out,unsigned capacity){
 if(capacity<24)return -1;alloc();mode=a[0];condition=a[8];effect=mode==3?a[9]:0;
 std::memset(ram,0x5a,SIZE);Sh4Context prepared;prepare(a,prepared);std::memcpy(baseline,ram,SIZE);
 uint32_t h0=0,n0=0,h1=0,n1=0;bool unchanged0=false,unchanged1=false;
 auto before=execute(a,prepared,false,a[11],h0,n0,unchanged0);std::memcpy(expected,ram,SIZE);
 auto after=execute(a,prepared,true,a[11],h1,n1,unchanged1);
 bool context=std::memcmp(&before.ctx,&after.ctx,sizeof(Sh4Context))==0;
 bool cycles=before.last==after.last&&before.mem==after.mem&&before.ratio==after.ratio;
 bool memory=std::memcmp(expected,ram,SIZE)==0;
 bool bus=before.bus.size()==after.bus.size()&&(before.bus.empty()||std::memcmp(before.bus.data(),after.bus.data(),before.bus.size()*sizeof(Event))==0);
 bool control=before.ending==after.ending&&before.epc==after.epc&&before.event==after.event&&before.irq==after.irq&&before.intevt==after.intevt;
 uint32_t diff=0xffffffffu;if(!memory){diff=0;while(expected[diff]==ram[diff])++diff;}
 uint32_t values[]={uint32_t(context&&cycles&&memory&&control&&(mode!=3||bus)),h1,n1,uint32_t(unchanged1),uint32_t(context),uint32_t(cycles),uint32_t(memory),uint32_t(bus),before.ending,after.ending,before.ctx.pc,after.ctx.pc,uint32_t(before.ctx.cycle_counter),uint32_t(after.ctx.cycle_counter),uint32_t(before.mem),uint32_t(after.mem),before.irq,after.irq,before.intevt,after.intevt,diff,uint32_t(before.bus.size()),uint32_t(after.bus.size()),get(COUNTER)};
 std::memcpy(out,values,sizeof(values));return 24;
}
