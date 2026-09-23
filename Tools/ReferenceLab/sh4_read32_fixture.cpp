// Laboratory only: actual candidate data helper versus original address-space reader.
#include "types.h"
#include "hw/sh4/sh4_if.h"
#include "hw/sh4/sh4_mem.h"
#include "hw/sh4/sh4_core.h"
#include "debug/gdb_server.h"
#include "mapped_read32.h"
#include <cstdlib>
#include <cstring>
#include <vector>

static uint8_t *bufferA,*bufferB;
static Sh4Context *current;
static uint32_t scenario,effect,activePage,activeMask,calls,sideEffect;
static std::vector<uint32_t> events;
static addrspace::handler handlerId;
static u32 DYNACALL alternate(uint32_t address);
static void effects(uint32_t address) {
    if(effect&1)current->pc+=4;
    if(effect&2)current->cycle_counter-=3;
    if(effect&16){sideEffect^=address+calls;current->r[7]^=sideEffect;bufferA[(address&activeMask)+3]^=0x5a;}
    if(effect&4)throw SH4ThrownException(address,Sh4Ex_TlbMissRead);
    if(effect&8)throw debugger::Stop();
}
static u32 DYNACALL handler(uint32_t address) {
    events.insert(events.end(),{address,current->pc,uint32_t(current->cycle_counter),calls,sideEffect});
    ++calls;
    if(scenario==3)addrspace::mapBlock(bufferB,activePage,activePage,activeMask);
    if(scenario==6)ReadMem32=alternate;
    effects(address);
    return 0x79b3d120u^(address>>1)^calls;
}
static u32 DYNACALL alternate(uint32_t address) {
    events.insert(events.end(),{address,current->pc,uint32_t(current->cycle_counter),0x80000000u|calls,sideEffect});
    ++calls;
    if(scenario==7)ReadMem32=addrspace::read32;
    effects(address);
    return 0xb1f08241u^(address>>1)^calls;
}
static void put(uint8_t *buffer,uint32_t address,uint32_t mask,uint32_t value) {
    const uint32_t offset=address&mask;
    for(unsigned i=0;i<4;++i)buffer[offset+i]=uint8_t(value>>(8*i));
}
static uint32_t readerId(){return ReadMem32==addrspace::read32?0:ReadMem32==alternate?1:2;}
template<class T>static void append(std::vector<uint8_t>&out,T value){for(unsigned i=0;i<sizeof(T);++i)out.push_back(uint8_t(uint64_t(value)>>(i*8)));}
// scenario,page,maskBits,offset,effect,implementation,rawValue,handlerID
extern "C" int vt_read32_fixture_case(const uint32_t *args,void *output,unsigned capacity) {
    if(!bufferA) {
        if(posix_memalign(reinterpret_cast<void**>(&bufferA),4096,(1u<<26)+4096)
            ||posix_memalign(reinterpret_cast<void**>(&bufferB),4096,(1u<<26)+4096))return -1;
        std::memset(bufferA,0,(1u<<26)+4096);std::memset(bufferB,0,(1u<<26)+4096);
    }
    scenario=args[0];activePage=args[1]&255;activeMask=args[2]==32?0xffffffffu:(uint32_t(1)<<args[2])-1;effect=args[4];calls=0;sideEffect=0x74513209;events.clear();
    uint32_t address=(activePage<<24)|(args[3]&0xffffffu);const uint32_t valueA=args[6],valueB=valueA^0x6da573c1;
    const auto offset=address&activeMask;
    if(offset>(1u<<26)-1)return -3;
    // Four adjacent bytes are deliberately contiguous across masked/page boundaries,
    // matching original readt<u32>; wrapping each byte would be a different operation.
    std::memset(bufferA+offset,0xca,16);std::memset(bufferB+offset,0x35,16);
    put(bufferA,address,activeMask,valueA);put(bufferB,address,activeMask,valueB);
    addrspace::init();handlerId=0;for(unsigned i=0;i<args[7];++i)handlerId=addrspace::registerHandler(nullptr,nullptr,handler,nullptr,nullptr,nullptr);
    addrspace::mapBlock(bufferA,activePage,activePage,activeMask);
    if(scenario==1 || scenario==3 || scenario==6)addrspace::mapHandler(handlerId,activePage,activePage);
    if(scenario==5) {
        const uint32_t originalPage=activePage^0x80u;
        addrspace::mapBlock(bufferA,originalPage,originalPage,activeMask);
        addrspace::mirrorMapping(activePage,originalPage,1);
        addrspace::mapBlock(bufferB,originalPage,originalPage,activeMask);
    }
    ReadMem32=(scenario==2 || scenario==7)?alternate:addrspace::read32;
    Sh4Context ctx;std::memset(&ctx,0,sizeof(ctx));current=&ctx;ctx.pc=0x8c123456;ctx.cycle_counter=100;ctx.r[7]=0x12131415;
    std::vector<uint8_t> result;const unsigned count=(scenario>=3&&scenario!=5)?4:1;
    append(result,count);
    for(unsigned step=0;step<count;++step) {
        if(scenario==4) {
            if(step==1)addrspace::mapBlock(bufferB,activePage,activePage,activeMask);
            if(step==2)addrspace::mapHandler(handlerId,activePage,activePage);
            if(step==3)addrspace::mapBlock(bufferA,activePage,activePage,activeMask);
        }
        uint32_t ending=0,exceptionPC=0,event=0,word=0;
        try{word=args[5]?vt_mapped_read32(address):ReadMem32(address);}
        catch(const SH4ThrownException&e){ending=1;exceptionPC=e.epc;event=e.expEvn;}
        catch(const debugger::Stop&){ending=2;}
        for(uint32_t x:{word,ending,exceptionPC,event,ctx.pc,uint32_t(ctx.cycle_counter),calls,readerId()})append(result,x);
    }
    auto context=reinterpret_cast<const uint8_t*>(&ctx);result.insert(result.end(),context,context+sizeof(ctx));
    append(result,uint32_t(events.size()));for(auto value:events)append(result,value);
    append(result,sideEffect);result.insert(result.end(),bufferA+offset,bufferA+offset+16);result.insert(result.end(),bufferB+offset,bufferB+offset+16);
    append(result,valueA);append(result,valueB);append(result,activeMask);
    if(result.size()>capacity)return -2;std::memcpy(output,result.data(),result.size());return result.size();
}
