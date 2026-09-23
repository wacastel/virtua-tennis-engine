// Laboratory only: actual candidate mapped-fetch helper versus original reader.
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
#include "mapped_fetch.h"
#include <cstdlib>
#include <cstring>
#include <vector>
#include <stdexcept>

static uint8_t *bufferA,*bufferB;
static Sh4Context *current;
static uint32_t scenario,effect,activePage,activeMask,calls;
static std::vector<uint32_t> events;
static addrspace::handler handlerId;
static u16 DYNACALL alternate(uint32_t address);
static u16 DYNACALL handler(uint32_t address) {
    events.insert(events.end(),{address,current->pc,uint32_t(current->cycle_counter),calls});
    ++calls;
    if(effect&1)current->pc+=4;
    if(effect&2)current->cycle_counter-=3;
    if(scenario==3)addrspace::mapBlock(bufferB,activePage,activePage,activeMask);
    if(scenario==6)IReadMem16=alternate;
    if(effect&4)throw SH4ThrownException(address,Sh4Ex_TlbMissRead);
    if(effect&8)throw debugger::Stop();
    return uint16_t(0x7900u^(address>>1)^calls);
}
static u16 DYNACALL alternate(uint32_t address) {
    events.insert(events.end(),{address,current->pc,uint32_t(current->cycle_counter),0x80000000u|calls});
    ++calls;
    if(effect&1)current->pc+=4;
    if(effect&2)current->cycle_counter-=3;
    if(scenario==7)IReadMem16=addrspace::read16;
    if(effect&4)throw SH4ThrownException(address,Sh4Ex_TlbMissRead);
    if(effect&8)throw debugger::Stop();
    return uint16_t(0xb100u^(address>>1)^calls);
}
static void put(uint8_t *buffer,uint32_t address,uint32_t mask,uint16_t value) {
    const uint32_t offset=address&mask;
    buffer[offset]=uint8_t(value);buffer[offset+1]=uint8_t(value>>8);
}
static uint32_t readerId() {return IReadMem16==addrspace::read16?0:IReadMem16==alternate?1:2;}
template<class T>static void append(std::vector<uint8_t>&out,T value){for(unsigned i=0;i<sizeof(T);++i)out.push_back(uint8_t(uint64_t(value)>>(i*8)));}
// scenario,page,maskBits,offset,effect,mmu,wrapper,implementation,seed,handlerID
extern "C" int vt_fetch_fixture_case(const uint32_t *args,void *output,unsigned capacity) {
    if(!bufferA) {
        if(posix_memalign(reinterpret_cast<void**>(&bufferA),4096,(1u<<26)+4096)
            ||posix_memalign(reinterpret_cast<void**>(&bufferB),4096,(1u<<26)+4096))return -1;
        std::memset(bufferA,0,(1u<<26)+4096);std::memset(bufferB,0,(1u<<26)+4096);
    }
    scenario=args[0];activePage=args[1]&255;activeMask=args[2]==32?0xffffffffu:(uint32_t(1)<<args[2])-1;effect=args[4];calls=0;events.clear();
    uint32_t address=(activePage<<24)|(args[3]&0xffffffu);const uint16_t valueA=uint16_t(0xc351u^(args[8]*0x311u));
    const uint16_t valueB=uint16_t(valueA^0x6da5u);
    put(bufferA,address,activeMask,valueA);put(bufferB,address,activeMask,valueB);
    addrspace::init();handlerId=0;for(unsigned i=0;i<args[9];++i)handlerId=addrspace::registerHandler(nullptr,handler,nullptr,nullptr,nullptr,nullptr);
    addrspace::mapBlock(bufferA,activePage,activePage,activeMask);
    if(scenario==1 || scenario==3 || scenario==6)addrspace::mapHandler(handlerId,activePage,activePage);
    if(scenario==5) {
        const uint32_t originalPage=activePage^0x80u;
        addrspace::mapBlock(bufferA,originalPage,originalPage,activeMask);
        addrspace::mirrorMapping(activePage,originalPage,1);
        addrspace::mapBlock(bufferB,originalPage,originalPage,activeMask);
    }
    IReadMem16=(scenario==2 || scenario==7)?alternate:addrspace::read16;
    Sh4Context ctx;std::memset(&ctx,0,sizeof(ctx));current=&ctx;ctx.pc=address;ctx.cycle_counter=100;
    mmuOn=args[5]!=0;Sh4Interpreter original;original.ctx=&ctx;
    std::vector<uint8_t> result;const unsigned count=(scenario==3||scenario==4||scenario==6||scenario==7)?4:1;
    append(result,count);
    for(unsigned step=0;step<count;++step) {
        ctx.pc=address;
        if(scenario==4) {
            if(step==1)addrspace::mapBlock(bufferB,activePage,activePage,activeMask);
            if(step==2)addrspace::mapHandler(handlerId,activePage,activePage);
            if(step==3)addrspace::mapBlock(bufferA,activePage,activePage,activeMask);
        }
        uint32_t ending=0,exceptionPC=0,event=0;uint16_t word=0;
        try {
            if(args[6])word=args[7]?vt_mapped_fetch(&ctx):original.ReadNexOp();
            else word=args[7]?vt_fetch_read16(address):IReadMem16(address);
        } catch(const SH4ThrownException&e){ending=1;exceptionPC=e.epc;event=e.expEvn;}
          catch(const debugger::Stop&){ending=2;}
        for(uint32_t x:{uint32_t(word),ending,exceptionPC,event,ctx.pc,uint32_t(ctx.cycle_counter),calls,readerId()})append(result,x);
    }
    auto context=reinterpret_cast<const uint8_t*>(&ctx);result.insert(result.end(),context,context+sizeof(ctx));
    append(result,uint32_t(events.size()));for(auto value:events)append(result,value);
    // Expose logical expectations without comparing host pointer addresses.
    append(result,uint32_t(valueA));append(result,uint32_t(valueB));append(result,activeMask);
    if(result.size()>capacity)return -2;std::memcpy(output,result.data(),result.size());return result.size();
}
