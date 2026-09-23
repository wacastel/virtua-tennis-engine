// Isolated laboratory fixture, never a shipping engine component.
// Calls the original Flycast interpreter and the actual compiled block entry.
#include "types.h"
#include "hw/sh4/sh4_if.h"
#include "hw/sh4/sh4_opcode_list.h"
#include "hw/sh4/sh4_sched.h"
#define private public
#define protected public
#include "hw/sh4/sh4_interpreter.h"
#undef protected
#undef private
#include "hw/sh4/sh4_core.h"
#include "hw/sh4/sh4_mem.h"
#include "hw/sh4/modules/mmu.h"
#include "debug/gdb_server.h"
#include "run_dispatch.h"
#include <map>
#include <vector>
#include <cstring>
#include <stdexcept>

struct FixturePlan { uint32_t pc, count, fetchCount, controlPC, targetRegister; uint16_t words[17]; };
#include "fixture_plans.inc"
extern "C" void vt_fixed_clear_error();
extern "C" const char *vt_fixed_error();
struct EndOfWindow {};
struct Event { uint32_t kind, address, size, pc, cycle; uint64_t value; };
static std::vector<Event> events;
static std::map<uint32_t,uint8_t> writes;
static Sh4Context *current;
static const FixturePlan *plan;
static uint32_t seed, flags, fetches, accesses, callTarget;
static int mutationIndex, mutationWord, fetchFault, dataFault;
static bool boundaryMutationSeen;
static Sh4Context boundaryContext;
static Sh4Cycles *currentCycles;
static unsigned boundaryLastUnit,boundaryMemOps,boundaryRatio,boundaryAccesses;
static std::map<uint32_t,uint8_t> boundaryWrites;
// The laboratory interpreter object keeps the exact original delay helper.
// Its instruction dispatcher selects the unchanged original or actual fixed
// body only at this test boundary, so nested delay execution is genuine too.
bool vt_fixture_fixed_delay=false;

static uint64_t readBytes(uint32_t address, unsigned size) {
    uint64_t value=0;
    for(unsigned i=0;i<size;i++) {
        auto it=writes.find(address+i);
        uint8_t byte=it==writes.end() ? uint8_t(((address+i)*0x9e3779b9u+seed*0x85ebca6bu)>>17) : it->second;
        value|=uint64_t(byte)<<(i*8);
    }
    return value;
}
static void event(unsigned kind,uint32_t address,unsigned size,uint64_t value) {
    events.push_back({kind,address,size,current->pc,uint32_t(current->cycle_counter),value});
}
static u16 DYNACALL fetch(uint32_t address) {
    uint32_t physical=address&0x1fffffff;
    unsigned index=fetches++;
    if (index>=4096 || ((flags&64) && index>=plan->fetchCount)) {
        event(4,address,2,0);throw EndOfWindow();
    }
    if (physical<plan->pc || physical>=plan->pc+plan->fetchCount*2 || (physical&1)) {
        event(4,address,2,0); throw EndOfWindow();
    }
    unsigned instruction=(physical-plan->pc)/2;
    if(plan->controlPC && physical==plan->controlPC)
        callTarget=plan->targetRegister==16?current->pr:current->r[plan->targetRegister];
    bool mutate=int(instruction)==mutationIndex && (!(flags&4096) || index>=plan->fetchCount);
    const bool postFetchMutation=(flags&(1048576u|2097152u))!=0;
    uint16_t word=mutate && !postFetchMutation ? uint16_t(mutationWord) : plan->words[instruction];
    if(mutate && postFetchMutation && !boundaryMutationSeen) {
        current->pc=(flags&2097152u)?0x0e000002u:((current->pc&0x1fffffffu)|((current->pc&0x80000000u)?0u:0x80000000u));
        boundaryMutationSeen=true;std::memcpy(&boundaryContext,current,sizeof(boundaryContext));
        boundaryLastUnit=unsigned(currentCycles->lastUnit);boundaryMemOps=currentCycles->memOps;boundaryRatio=currentCycles->cpuRatio;
        boundaryAccesses=accesses;boundaryWrites=writes;
    }
    if((flags&65536) && (physical==0x0c0a53deu || (plan->controlPC && physical==plan->controlPC+2)))current->pc=0x0e000002u;
    if((flags&131072) && plan->controlPC && physical==plan->controlPC+2) {
        if(plan->targetRegister<16)current->r[plan->targetRegister]=0x0c234560u;
        current->pr=0xdeadbeefu;
    }
    // Conditional-terminal fixtures supply both possible pre-branch T values
    // through the same redirected fetch callback in original and fixed runs.
    if(instruction+1==plan->count && (flags&48))current->sr.T=(flags&32)!=0;
    event(0,address,2,word);
    if(int(index)==fetchFault) {
        if(flags&32768)throw debugger::Stop();
        throw SH4ThrownException(address,Sh4Ex_TlbMissRead);
    }
    return word;
}
static uint64_t read(uint32_t address,unsigned size) {
    unsigned index=accesses++; uint64_t value=readBytes(address,size);
    if((index==0 || (flags&8192)) && (flags&384))value=(flags&256)?1:0;
    if(flags&16384)value=index<4?1:0;
    uint32_t instructionPC=(current->pc-2)&0x1fffffffu;
    if((flags&262144) && (instructionPC==0x0c0bf334u || instructionPC==0x0c0bf33cu))value=0x0c123450u;
    event(1,address,size,value);
    if((flags&524288) && instructionPC==0x0c0bf338u)throw SH4ThrownException(current->pc-2,Sh4Ex_AddressErrorRead);
    if(int(index)==dataFault) {
        if(flags&32768)throw debugger::Stop();
        throw SH4ThrownException(current->pc-2,Sh4Ex_TlbMissRead);
    }
    if(flags&4) current->cycle_counter-=2;
    if((flags&8) && index==0) current->pc+=2;
    return value;
}
static void write(uint32_t address,unsigned size,uint64_t value) {
    unsigned index=accesses++;event(2,address,size,value);
    if(flags&512)throw SH4ThrownException(current->pc-2,Sh4Ex_AddressErrorWrite);
    if(int(index)==dataFault) {
        if(flags&32768)throw debugger::Stop();
        throw SH4ThrownException(current->pc-2,Sh4Ex_TlbMissWrite);
    }
    for(unsigned i=0;i<size;i++)writes[address+i]=uint8_t(value>>(8*i));
    if(flags&4) current->cycle_counter-=3;
    if((flags&8) && index==0) current->pc+=2;
}
static u8 DYNACALL r8(u32 a){return read(a,1);} static u16 DYNACALL r16(u32 a){return read(a,2);}
static u32 DYNACALL r32(u32 a){return read(a,4);} static u64 DYNACALL r64(u32 a){return read(a,8);}
static void DYNACALL w8(u32 a,u8 v){write(a,1,v);} static void DYNACALL w16(u32 a,u16 v){write(a,2,v);}
static void DYNACALL w32(u32 a,u32 v){write(a,4,v);} static void DYNACALL w64(u32 a,u64 v){write(a,8,v);}

static void prepare(Sh4Context &ctx,unsigned value,uint32_t pc,int budget) {
    std::memset(&ctx,0,sizeof(ctx));uint32_t random=value*0x9e3779b9u+0x1234567u;
    auto next=[&](){random^=random<<13;random^=random>>17;random^=random<<5;return random;};
    for(unsigned i=0;i<16;i++) {
        ctx.r[i]=0x0c400000u+(next()&0x1fff8u);
        ctx.fr[i]=float(int(next()%20001)-10000)/127.0f;
        ctx.xf[i]=float(int(next()%20001)-10000)/251.0f;
    }
    for(unsigned i=0;i<8;i++)ctx.r_bank[i]=next();
    ctx.mac.full=(uint64_t(next())<<32)|next();ctx.gbr=0x0c600000u;
    ctx.ssr=next();ctx.spc=next();ctx.sgr=next();ctx.dbr=next();ctx.vbr=next();ctx.pr=next();ctx.fpul=next();
    if(flags&262144){ctx.r[1]=ctx.r[13]=ctx.pr=0x0c123450u;}
    // The pinned original MAC.L explicitly rejects saturating S=1. Stay in
    // its supported arithmetic domain instead of turning a fixture into a
    // deliberate original-core assertion failure.
    ctx.sr.setFull((next()&~0x8002u)|((flags&2)?0x8000u:0));
    ctx.fpscr.full=((value&1)?0x80000:0)|((value&2)?0x100000:0);
    ctx.pc=pc;ctx.cycle_counter=budget;ctx.CpuRunning=true;ctx.restoreHostRoundingMode();
}
template<class T> static void append(std::vector<uint8_t>& out,T value) {
    for(unsigned i=0;i<sizeof(T);i++)out.push_back(uint8_t(uint64_t(value)>>(i*8)));
}

// args: plan, seed, signed budget, warm metadata count, virtual alias, flags,
// signed mutation index/word, signed fetch/data fault indices, implementation.
extern "C" int vt_block_fixture_case(const uint32_t *args,void *output,unsigned capacity) {
    if(args[0]>=sizeof(fixturePlans)/sizeof(fixturePlans[0]))return -1;
    plan=&fixturePlans[args[0]];seed=args[1];flags=args[5];mutationIndex=int(args[6]);mutationWord=int(args[7]);
    fetchFault=int(args[8]);dataFault=int(args[9]);fetches=accesses=callTarget=0;events.clear();writes.clear();boundaryMutationSeen=false;boundaryWrites.clear();
    Sh4Context ctx;current=&ctx;prepare(ctx,seed,plan->pc|args[4],int(args[2]));
    Sh4Interpreter original;original.ctx=&ctx;original.sh4cycles.init(&ctx);Sh4Interpreter::Instance=&original;currentCycles=&original.sh4cycles;
    mmuOn=(flags&1)!=0;
    for(unsigned i=0;i<args[3];i++)original.sh4cycles.executeCycles(i&1 ? 0x300c : 0x6012);
    ctx.cycle_counter=int(args[2]);vt_fixed_clear_error();
    vt_fixture_fixed_delay=args[10]!=0;
    IReadMem16=fetch;ReadMem8=r8;ReadMem16=r16;ReadMem32=r32;ReadMem64=r64;
    WriteMem8=w8;WriteMem16=w16;WriteMem32=w32;WriteMem64=w64;
    unsigned ending=0,exceptionPC=0,exceptionEvent=0;
    try {
        // Exactly the original Run inner do/while. Its unchanged scheduler and
        // exception-delivery outer loop are deliberately outside this fixture.
        if(flags&2048)original.ExecuteDelayslot();
        else do {
            uint16_t word=original.ReadNexOp();
            if(args[10])vt_run_execute(&ctx,word,original.sh4cycles);
            else original.ExecuteOpcode(word);
        } while(ctx.cycle_counter>0);
    } catch(const EndOfWindow&) {ending=1;}
      catch(const SH4ThrownException& e){ending=2;exceptionPC=e.epc;exceptionEvent=e.expEvn;}
      catch(const debugger::Stop&){ending=5;}
      catch(const FlycastException&){ending=std::strlen(vt_fixed_error())?3:4;}
    std::vector<uint8_t> bytes;
    for(unsigned value:{ending,exceptionPC,exceptionEvent,unsigned(original.sh4cycles.lastUnit),
        unsigned(original.sh4cycles.memOps),unsigned(original.sh4cycles.cpuRatio),fetches,accesses,unsigned(sizeof(ctx))})append(bytes,value);
    append(bytes,uint32_t(ctx.cycle_counter));append(bytes,ctx.pc);
    append(bytes,ctx.r[1]);append(bytes,ctx.pr);append(bytes,callTarget);
    append(bytes,ctx.r[13]);
    auto p=reinterpret_cast<const uint8_t*>(&ctx);bytes.insert(bytes.end(),p,p+sizeof(ctx));
    append(bytes,uint32_t(events.size()));
    for(const auto&e:events){append(bytes,e.kind);append(bytes,e.address);append(bytes,e.size);append(bytes,e.pc);append(bytes,e.cycle);append(bytes,e.value);}
    append(bytes,uint32_t(writes.size()));for(const auto &w:writes){append(bytes,w.first);append(bytes,w.second);}
    append(bytes,uint32_t(boundaryMutationSeen));
    append(bytes,uint32_t(boundaryMutationSeen && std::memcmp(&ctx,&boundaryContext,sizeof(ctx))==0
        && unsigned(original.sh4cycles.lastUnit)==boundaryLastUnit && original.sh4cycles.memOps==boundaryMemOps
        && original.sh4cycles.cpuRatio==boundaryRatio && accesses==boundaryAccesses && writes==boundaryWrites));
    Sh4Interpreter::Instance=nullptr;
    if(bytes.size()>capacity)return -2;std::memcpy(output,bytes.data(),bytes.size());return bytes.size();
}
extern "C" unsigned vt_block_fixture_count(){return sizeof(fixturePlans)/sizeof(fixturePlans[0]);}
