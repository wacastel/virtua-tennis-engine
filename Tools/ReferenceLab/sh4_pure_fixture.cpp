// Actual compiled pure-helper vs original-operation mapped-RAM oracle. No original core source is modified.
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
#include <cstring>
#include <cstdlib>
#include <vector>
#include <algorithm>
#include <new>
struct Segment {u32 pc,count,maxCost;bool whole;bool(*probe)(Sh4Context*,Sh4Cycles&);const u16*words;const u16*mutants;};
#include "segments.inc"
namespace ggpo {extern bool inRollback;}
bool vt_fixture_fixed_delay=false;
extern "C" void vt_fixed_clear_error();
extern "C" const char* vt_fixed_error();
static constexpr u32 SIZE=32u<<20;
static u8 *ram,*baseline,*expected,*other;
static Sh4Context* current;
static u32 condition,aux,accesses;struct Event{u32 address,value,pc,cycles;};static std::vector<Event>events;
static u16 DYNACALL fetchAlt(u32 a){auto v=addrspace::read16(a);events.push_back({a,v,current->pc,u32(current->cycle_counter)});++accesses;if(condition==24&&accesses==aux)current->cycle_counter-=2;if(condition==25&&accesses==aux)throw SH4ThrownException(current->pc-2,Sh4Ex_TlbMissRead);if(condition==26&&accesses==aux)throw debugger::Stop();return v;}
static u32 DYNACALL readAlt(u32 a){return addrspace::read32(a);}static void DYNACALL writeAlt(u32 a,u32 v){addrspace::write32(a,v);}
static u16 DYNACALL deviceFetch(u32 a){auto v=*reinterpret_cast<u16*>(ram+(a&(SIZE-1)));events.push_back({a,v,current->pc,u32(current->cycle_counter)});++accesses;return v;}
struct State{Sh4Context ctx;int last,mem,ratio;u32 ending,epc,event,irq,intevt,hits,unchanged;std::vector<Event>bus;};
static void alloc(){if(ram)return;for(auto p:{&ram,&baseline,&expected,&other})if(posix_memalign(reinterpret_cast<void**>(p),4096,SIZE))std::abort();std::memset(other,0x55,SIZE);if(posix_memalign(reinterpret_cast<void**>(&p_sh4rcb),64,sizeof(Sh4RCB)))std::abort();std::memset(p_sh4rcb,0,sizeof(Sh4RCB));}
static void mappings(){addrspace::init();mem_b.setRegion(ram,SIZE);settings.platform.system=DC_PLATFORM_NAOMI;settings.platform.ram_size=SIZE;settings.platform.ram_mask=SIZE-1;for(unsigned a:{0u,0x80u,0xa0u})addrspace::mapBlockMirror(ram,0x0c|a,0x0f|a,SIZE);IReadMem16=addrspace::read16;ReadMem32=addrspace::read32;WriteMem32=addrspace::write32;mmuOn=condition==1;
 if(condition==2||condition==24||condition==25||condition==26)IReadMem16=fetchAlt;if(condition==3)ReadMem32=readAlt;if(condition==4)WriteMem32=writeAlt;if(condition==13)settings.platform.ram_size=16u<<20;if(condition==14)for(unsigned a:{0x0cu,0x8cu,0xacu})addrspace::mapBlock(other,a,a,SIZE-1);if(condition==15)for(unsigned a:{0x0cu,0x8cu,0xacu})addrspace::mapBlock(ram,a,a,65535);if(condition==16){auto h=addrspace::registerHandler(nullptr,deviceFetch,nullptr,nullptr,nullptr,nullptr);for(unsigned a:{0x0cu,0x8cu,0xacu})addrspace::mapHandler(h,a,a);}if(condition==27)settings.platform.ram_mask=65535;if(condition==19)addrspace::mapBlockMirror(ram,0x4c,0x4f,SIZE);
 config::ThreadedRendering=condition==5;config::NetworkEnable=condition==6;config::GGPOEnable=condition==7;settings.network.online=condition==8;ggpo::inRollback=condition==9;settings.naomi.multiboard=condition==10;settings.naomi.slave=condition==11;settings.naomi.drivingSimSlave=condition==12;
}
static Sh4Context prepare(const u32*a,const Segment&s){Sh4Context c{};u32 seed=a[3]*0x9e3779b9u+0xdeadbeefu;auto random=[&](){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;};for(unsigned i=0;i<16;++i){c.r[i]=random();c.fr_hex(i)=random();std::memcpy(&c.xf[i],&c.r[i],4);}for(unsigned i=0;i<8;++i)c.r_bank[i]=random();c.mac.full=(u64(random())<<32)|random();c.fpul=random();c.pr=random();c.gbr=random();c.vbr=0x0c600000;c.sr.setFull((random()&0x30000003u)|0x40000000u);c.sr.FD=a[8];c.sr.IMASK=0;c.sr.BL=0;c.sr.RB=0;c.old_sr.status=c.sr.status;c.fpscr.full=a[7]&0x003fffffu;c.old_fpscr=c.fpscr;c.pc=s.pc|a[4];c.cycle_counter=int(a[2]);c.CpuRunning=1;c.sh4_sched_next=448*100;
 if(condition==18)c.pc+=2;if(condition==19)c.pc=s.pc|0x40000000u;
 std::memcpy(ram+(s.pc&(SIZE-1)),s.words,s.count*2);if(condition==22){const u16 word=s.mutants[aux%s.count];std::memcpy(ram+(s.pc&(SIZE-1))+2*(aux%s.count),&word,2);}return c;}
static State execute(const u32*a,const Segment&s,const Sh4Context&prepared,bool native){std::memcpy(ram,baseline,SIZE);Sh4cntx=prepared;auto&ctx=Sh4cntx;current=&ctx;events.clear();accesses=0;mappings();interrupts_reset();if(a[11])SetInterruptPend(sh4_IRL_9);CCN_INTEVT=0;Sh4Interpreter original;original.ctx=&ctx;new(&original.sh4cycles)Sh4Cycles(a[12]);original.sh4cycles.init(&ctx);original.sh4cycles.lastUnit=static_cast<sh4_eu>(a[5]);original.sh4cycles.memOps=int(a[6]);Sh4Interpreter::Instance=&original;vt_fixture_fixed_delay=native;vt_fixed_clear_error();ctx.restoreHostRoundingMode();State result{};auto&cycles=original.sh4cycles;
 try{u16 first=original.ReadNexOp();Sh4Context snapshot=ctx;int last=cycles.lastUnit,mem=cycles.memOps;auto busBefore=events.size();Sh4Context* active=&ctx;Sh4Cycles* activeCycles=&cycles;
  if(condition==20){active=new(ram+(8u<<20))Sh4Context(ctx);}
  if(condition==21){activeCycles=new(ram+(9u<<20))Sh4Cycles(a[12]);activeCycles->init(&ctx);activeCycles->lastUnit=cycles.lastUnit;activeCycles->memOps=cycles.memOps;}
  if(condition==17)cycles.ctx=&snapshot;else if(condition==20)cycles.ctx=active;
  if(a[1]==0){if(native)result.hits=s.probe(active,*activeCycles);else if(a[13]){u16 op=first;for(unsigned i=0;i<s.count;++i){original.ExecuteOpcode(op);if(i+1<s.count)op=original.ReadNexOp();}}}
  else if((a[1]==2||(a[1]==3&&s.whole))&&native){vt_run_execute(&ctx,first,cycles);}
  else{bool done=native&&s.probe(active,*activeCycles);result.hits=done;if(!done){u16 op=first;for(unsigned i=0;i<s.count;++i){if(!native&&a[1]==3&&i==aux)throw FlycastException("expected fixed word guard");if(native)vt_sh4_execute(&ctx,op,cycles);else original.ExecuteOpcode(op);if(ctx.cycle_counter<=0||i+1==s.count)break;op=original.ReadNexOp();}}}
  result.unchanged=std::memcmp(&ctx,&snapshot,sizeof(ctx))==0&&cycles.lastUnit==last&&cycles.memOps==mem&&events.size()==busBefore;
  if(a[11]&&ctx.cycle_counter>0){cycles.addCycles(ctx.cycle_counter);ctx.cycle_counter+=SH4_TIMESLICE;result.irq=UpdateSystem_INTC();result.intevt=CCN_INTEVT;}
 }catch(const SH4ThrownException&e){result.ending=1;result.epc=e.epc;result.event=e.expEvn;}catch(const debugger::Stop&){result.ending=2;}catch(const FlycastException&){result.ending=3;}
 result.ctx=ctx;result.last=cycles.lastUnit;result.mem=cycles.memOps;result.ratio=cycles.cpuRatio;result.bus=events;Sh4Interpreter::Instance=nullptr;return result;}
// id,mode,budget,seed,alias,lastUnit,memOps,FPSCR,FD,condition,aux,IRQ,ratio,expectFast
extern "C" int vt_pure_fixture_case(const u32*a,u32*out,unsigned capacity){if(capacity<24||a[0]>=std::size(segments))return -1;alloc();condition=a[9];aux=a[10];const auto&s=segments[a[0]];std::memset(ram,0x5a,SIZE);auto prepared=prepare(a,s);std::memcpy(baseline,ram,SIZE);auto old=execute(a,s,prepared,false);std::memcpy(expected,ram,SIZE);auto now=execute(a,s,prepared,true);bool ctx=std::memcmp(&old.ctx,&now.ctx,sizeof(old.ctx))==0,cyc=old.last==now.last&&old.mem==now.mem&&old.ratio==now.ratio,mem=std::memcmp(expected,ram,SIZE)==0,bus=old.bus.size()==now.bus.size()&&(old.bus.empty()||std::memcmp(old.bus.data(),now.bus.data(),old.bus.size()*sizeof(Event))==0),control=old.ending==now.ending&&old.epc==now.epc&&old.event==now.event&&old.irq==now.irq&&old.intevt==now.intevt;
 u32 diff=0xffffffffu;if(!mem){diff=0;while(expected[diff]==ram[diff])++diff;}u32 values[]={u32(ctx&&cyc&&mem&&control&&bus),now.hits,now.unchanged,u32(ctx),u32(cyc),u32(mem),u32(bus),old.ending,now.ending,old.ctx.pc,now.ctx.pc,u32(old.ctx.cycle_counter),u32(now.ctx.cycle_counter),u32(old.last),u32(now.last),u32(old.mem),u32(now.mem),old.irq,now.irq,old.epc,now.epc,old.event,now.event,diff};std::memcpy(out,values,sizeof(values));return 24;}
