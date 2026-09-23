"""Review-only bounded optimization configuration validation, without emission.

The caller authenticates original source/media first, then supplies its exact
ROM-derived images and offline original operation metadata and handler bodies.
This module admits no instruction/image not already in the full fixed map.
"""
from __future__ import annotations
import hashlib,re,struct

REPEAT_SITES={0x0c0a53d8,0x0c0a3f9a}
DELAY_SITES={0x0c0a53dc:'bf_s',0x0c0bf336:'jsr',0x0c0bf33e:'rts',0x0c0be080:'rts',0x0c0c183a:'jsr'}
CHAIN_NODES=(0x0c0c183a,0x0c0bf320,0x0c0be080,0x0c0bf33a,0x0c0c183e,0x0c0c184e)
REJECT_BODY=re.compile(r'ctx->pc\s*(?:[+*/|&^\-]?=)|branch_target_|executeDelaySlot|Update|Exception|debugger|UTLB|CCN_|ocache|icache|iNimp|restoreHost|rounding|sq_buffer|doSqWrite|sh4_sched|CpuRunning|Sh4Interpreter')

def require(condition,message):
    if not condition:raise ValueError(message)

def digest(data):return hashlib.sha256(data).hexdigest()

def validate(config,images,metadata,original_bodies):
    """Return authenticated records plus original offline metadata; no writes."""
    require(config['schema']==1 and config['optimizationOnly'] is True,'Unsupported optimization schema')
    require(config['traceRequiredForGeneration'] is False,'Generation must remain ROM-only')
    require((config['maximumBlocks'],config['maximumGameBlocks'],config['maximumBIOSBlocks'],config['maximumInstructionsPerBlock'])==(384,256,128,16),'Optimization bounds changed')
    image_by_name={name:(base,data) for name,base,data in images}
    def span(record,count_key='instructions',offset_key='sourceOffset',pc_key='pc',hash_key='sha256'):
        name=record['image'];require(name in image_by_name,'Unknown original image')
        base,data=image_by_name[name];pc=int(record[pc_key],16);offset=record[offset_key];count=record[count_key]
        require(isinstance(offset,int) and isinstance(count,int) and count>0 and offset>=0 and offset%2==0,'Invalid source span')
        require(pc==base+offset and offset+2*count<=len(data),'Mapped source span differs')
        raw=data[offset:offset+2*count];require(digest(raw)==record[hash_key],'Original instruction span changed')
        return pc,struct.unpack('<'+'H'*count,raw)
    delays={}
    for site in config['exactDelaySites']:
        pc=int(site['branchPC'],16);require(pc in DELAY_SITES and site['kind']==DELAY_SITES[pc] and pc not in delays,'Unexpected exact delay site')
        require(int(site['slotPC'],16)==pc+2,'Delay slot is not adjacent')
        common={'image':site['image'],'instructions':1}
        _,(word,)=span(common|{'pc':site['branchPC'],'sourceOffset':site['branchSourceOffset'],'sha256':site['branchSHA256']})
        _,(slot,)=span(common|{'pc':site['slotPC'],'sourceOffset':site['slotSourceOffset'],'sha256':site['slotSHA256']})
        require((site['kind']=='rts' and word==0x000b) or (site['kind']=='jsr' and word&0xf0ff==0x400b) or (site['kind']=='bf_s' and word&0xff00==0x8f00),'Original delayed branch class changed')
        row=metadata[slot];require(not row['floating'] and row['handler'] in original_bodies and not REJECT_BODY.search(original_bodies[row['handler']]),'Delay slot is outside approved simple operation scope')
        delays[pc]=site|{'word':word,'slotWord':slot,'metadata':metadata[word],'slotMetadata':row}
    require(set(delays)==set(DELAY_SITES),'Missing exact delay site')
    blocks=[];seen=set();counts={'game0':0,'bios_ram':0};repeats=set()
    for record in config['blocks']:
        pc,words=span(record);name=record['image'];require(name in counts and 2<=len(words)<=16,'Block bounds changed')
        key=(name,pc);require(key not in seen,'Duplicate block');seen.add(key);counts[name]+=1
        if name=='bios_ram':require(record['romOffset']==record['sourceOffset']+0x100,'BIOS alias source offset differs')
        for index,word in enumerate(words):
            current=pc+2*index;row=metadata[word];last=index==len(words)-1
            terminal=last and (word&0xff00 in (0x8900,0x8b00) or current in delays)
            require(terminal or (row['handler'] in original_bodies and not REJECT_BODY.search(original_bodies[row['handler']])),'Unapproved nonterminal operation')
        if record.get('repeatExactSelfLoop',False):
            require(pc in REPEAT_SITES and words[-1]&0xff00 in (0x8900,0x8b00,0x8f00),'Unexpected repeated branch')
            displacement=words[-1]&255;displacement=displacement-256 if displacement>=128 else displacement
            require(pc+2*(len(words)-1)+4+2*displacement==pc,'Repeated branch no longer returns to entry')
            repeats.add(pc)
        blocks.append(record|{'words':words,'metadata':[metadata[w] for w in words]})
    require(counts=={'game0':256,'bios_ram':128} and repeats==REPEAT_SITES,'Selected block or repeat set changed')
    overrides=[]
    for record in config['runEntryOverrides']:
        pc,words=span(record);require(len(words)==1 and pc in DELAY_SITES and pc!=0x0c0a53dc,'Unexpected Run entry override')
        overrides.append(record|{'word':words[0],'metadata':metadata[words[0]]})
    require({int(x['pc'],16) for x in overrides}==set(DELAY_SITES)-{0x0c0a53dc} and len(overrides)==4,'Missing Run override')
    return {'blocks':blocks,'delays':delays,'runEntryOverrides':overrides,'originalBodyHashes':{h:digest(body.encode()) for h,body in original_bodies.items()}}
