// Isolated six-node Run-chain laboratory, using actual candidate objects.
#include "types.h"
#include "hw/sh4/sh4_if.h"
#define private public
#define protected public
#include "hw/sh4/sh4_interpreter.h"
#undef protected
#undef private
#include "hw/sh4/sh4_mem.h"
#include "hw/sh4/modules/mmu.h"
#include "hw/sh4/sh4_core.h"
#include "debug/gdb_server.h"
#include "run_dispatch.h"
#include <map>
#include <vector>
#include <cstring>
struct ChainWord { uint32_t pc; uint16_t word; };
#include "chain_words.inc"
extern "C" void vt_fixed_clear_error();
extern "C" const char *vt_fixed_error();
bool vt_fixture_fixed_delay=false;
struct EndWindow{};struct Ready{};
struct Event {uint32_t kind,address,size,pc,cycles;uint64_t value;};
static std::vector<Event>events;
static std::map<uint32_t,uint8_t>memory;
static Sh4Context*context;
static const uint32_t *args;
static unsigned fetches,accesses,edgeSeen,speculative;
static bool preparing,inNative;
static uint32_t preparePC;
static constexpr uint32_t STACK=0x0c400100,COUNTER=0x0c500000,COMPARE=0x0c500010,TABLE=0x0c28a25c,EXIT=0x0c123450;
static u16 DYNACALL fetchAlt(u32);
static uint32_t physical(uint32_t a){return a&0x1fffffffu;}
static void store(uint32_t address,unsigned size,uint64_t value){for(unsigned i=0;i<size;++i)memory[physical(address+i)]=uint8_t(value>>(i*8));}
static uint64_t load(uint32_t address,unsigned size){uint64_t v=0;for(unsigned i=0;i<size;++i)v|=uint64_t(memory[physical(address+i)])<<(i*8);return v;}
static void event(unsigned kind,uint32_t address,unsigned size,uint64_t value){if(!preparing)events.push_back({kind,address,size,context->pc,uint32_t(context->cycle_counter),value});}
static u16 fetchCommon(u32 address,bool alternate){
    uint32_t pc=physical(address);
    if(preparing && pc==preparePC)throw Ready();
    ++fetches;
    if(fetches>4096){event(5,address,2,0);throw EndWindow();}
    const ChainWord *entry=nullptr;for(const auto &word:chainWords)if(word.pc==pc){entry=&word;break;}
    if(!entry){if(inNative && !(args[4]&(128|512)))++speculative;event(5,address,2,0);throw EndWindow();}
    uint16_t word=entry->word;
    const uint32_t edgePC=chainNodes[(args[5]+1)%6];
    bool edge=!preparing && fetches>1 && pc==edgePC && ++edgeSeen==args[6];
    // Only increment an edge occurrence for its exact successor address.
    if(edge){
        if(args[4]&512)word=uint16_t(args[9]);
        if(args[4]&128)context->pc=(args[8]|args[3])+2;
        if(args[4]&256)context->pc=0x0e000002;
    }
    event(alternate?4:0,address,2,word);
    if(edge && (args[4]&1024))throw SH4ThrownException(address,Sh4Ex_TlbMissRead);
    if(edge && (args[4]&2048))throw debugger::Stop();
    return word;
}
static u16 DYNACALL fetch(u32 a){return fetchCommon(a,false);}static u16 DYNACALL fetchAlt(u32 a){return fetchCommon(a,true);}
static uint64_t read(uint32_t address,unsigned size){
    unsigned index=accesses++;uint64_t value=load(address,size);event(1,address,size,value);
    if(!preparing && int(index)==int(args[7]))throw SH4ThrownException(context->pc-2,Sh4Ex_TlbMissRead);
    if(!preparing && (args[4]&4))context->cycle_counter-=2;
    return value;
}
static void write(uint32_t address,unsigned size,uint64_t value){
    unsigned index=accesses++;event(2,address,size,value);
    if(!preparing && int(index)==int(args[7]))throw SH4ThrownException(context->pc-2,Sh4Ex_TlbMissWrite);
    store(address,size,value);
    if(!preparing && (args[4]&4))context->cycle_counter-=3;
    if(!preparing && physical(address)==COUNTER){
        if(args[4]&8192)store(TABLE+56,4,EXIT|args[3]);
        if(args[4]&16384)store(COMPARE,4,0x1235);
        if(args[4]&65536)IReadMem16=fetchAlt;
    }
}
static u8 DYNACALL r8(u32 a){return read(a,1);}static u16 DYNACALL r16(u32 a){return read(a,2);}static u32 DYNACALL r32(u32 a){return read(a,4);}static u64 DYNACALL r64(u32 a){return read(a,8);}
static void DYNACALL w8(u32 a,u8 v){write(a,1,v);}static void DYNACALL w16(u32 a,u16 v){write(a,2,v);}static void DYNACALL w32(u32 a,u32 v){write(a,4,v);}static void DYNACALL w64(u32 a,u64 v){write(a,8,v);}
template<class T>static void append(std::vector<uint8_t>&out,T v){for(unsigned i=0;i<sizeof(T);++i)out.push_back(uint8_t(uint64_t(v)>>(8*i)));}
// start,loops,budget,alias,flags,edge,occurrence,dataFault,alternatePC,word,implementation
extern "C" int vt_chain_fixture_case(const uint32_t *parameters,void *output,unsigned capacity){
    args=parameters;memory.clear();events.clear();fetches=accesses=edgeSeen=speculative=0;inNative=false;
    Sh4Context ctx;std::memset(&ctx,0,sizeof(ctx));context=&ctx;
    ctx.pc=chainNodes[0]|args[3];ctx.r[13]=chainNodes[1]|args[3];ctx.r[14]=0x1234;ctx.r[15]=STACK|args[3];ctx.cycle_counter=100000;ctx.CpuRunning=true;
    ctx.sr.FD=(args[4]&2)!=0;ctx.restoreHostRoundingMode();mmuOn=(args[4]&1)!=0;
    store(STACK,4,COUNTER);store(STACK+4,4,COMPARE);store(COUNTER,4,args[1]);store(COMPARE,4,0x1234);
    store(0x0c0bf360,4,TABLE);store(TABLE+56,4,chainNodes[2]|args[3]);store(TABLE+60,4,0x9876);
    IReadMem16=fetch;ReadMem8=r8;ReadMem16=r16;ReadMem32=r32;ReadMem64=r64;WriteMem8=w8;WriteMem16=w16;WriteMem32=w32;WriteMem64=w64;
    Sh4Interpreter original;original.ctx=&ctx;original.sh4cycles.init(&ctx);Sh4Interpreter::Instance=&original;vt_fixture_fixed_delay=false;
    preparing=true;preparePC=chainNodes[args[0]];
    try{for(;;){uint16_t op=original.ReadNexOp();original.ExecuteOpcode(op);}}catch(const Ready&){ctx.pc-=2;}catch(...){Sh4Interpreter::Instance=nullptr;return -3;}
    preparing=false;fetches=accesses=edgeSeen=0;events.clear();ctx.cycle_counter=int(args[2]);
    if(args[4]&8)store(COMPARE,4,0x1235);
    if(args[4]&16)store(TABLE+56,4,EXIT|args[3]);
    if(args[4]&32)ctx.r[13]=EXIT|args[3];
    if(args[4]&64){store(STACK-4,4,EXIT|args[3]);if(args[0]==2)ctx.pr=EXIT|args[3];}
    vt_fixture_fixed_delay=args[10]!=0;vt_fixed_clear_error();
    unsigned ending=0,exceptionPC=0,exceptionEvent=0;
    try{do{uint16_t op=original.ReadNexOp();if(args[10]){inNative=true;vt_run_execute(&ctx,op,original.sh4cycles);inNative=false;}else original.ExecuteOpcode(op);}while(ctx.cycle_counter>0);}
    catch(const EndWindow&){ending=1;}catch(const SH4ThrownException&e){ending=2;exceptionPC=e.epc;exceptionEvent=e.expEvn;}
    catch(const debugger::Stop&){ending=3;}catch(const FlycastException&){ending=std::strlen(vt_fixed_error())?4:5;}
    inNative=false;std::vector<uint8_t>result;
    for(unsigned v:{ending,exceptionPC,exceptionEvent,unsigned(ctx.cycle_counter),ctx.pc,fetches,accesses,edgeSeen,speculative,unsigned(load(COUNTER,4)),unsigned(original.sh4cycles.lastUnit),unsigned(original.sh4cycles.memOps),unsigned(original.sh4cycles.cpuRatio)})append(result,v);
    auto bytes=reinterpret_cast<const uint8_t*>(&ctx);result.insert(result.end(),bytes,bytes+sizeof(ctx));append(result,unsigned(events.size()));
    for(const auto&e:events){append(result,e.kind);append(result,e.address);append(result,e.size);append(result,e.pc);append(result,e.cycles);append(result,e.value);}
    append(result,unsigned(memory.size()));for(auto [a,v]:memory){append(result,a);append(result,v);}Sh4Interpreter::Instance=nullptr;
    if(result.size()>capacity)return -2;std::memcpy(output,result.data(),result.size());return result.size();
}
