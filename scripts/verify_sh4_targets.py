#!/usr/bin/env python3
"""Authenticate every linked immutable SH4 target against canonical generation."""
from __future__ import annotations
import argparse,hashlib,json,re,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(condition,message):
    if not condition:raise RuntimeError(message)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=ROOT/'build/native/libvirtua_tennis.dylib')
    parser.add_argument('--output',type=Path,default=ROOT/'Documentation/sh4-target-acceptance.json')
    args=parser.parse_args();library=args.library.resolve();output=args.output.resolve()
    from native_provenance import audit_cpu_inputs,verify_snapshot
    audit=audit_cpu_inputs(ROOT,{'sh4'})
    cpu_path=ROOT/'build/native/cpu/sh4/manifest.json';generation_path=ROOT/'build/generated/sh4/manifest.json';generated=generation_path.parent
    cpu=json.loads(cpu_path.read_text());generation=json.loads(generation_path.read_text());run=cpu['runOptimization'];plan_path=ROOT/run['plan'];plan=json.loads(plan_path.read_text())
    library_hash=sha(library);engine_path=library.parent/'manifest.json';engine_hash=sha(engine_path);engine=json.loads(engine_path.read_text())
    require(engine['productSHA256']==library_hash and (ROOT/engine['product'].replace('PROJECT/','')).resolve()==library,'Library is not bound to its canonical link manifest')
    require(engine['CPUManifests'].get('sh4')==sha(cpu_path),'Linked SH4 manifest differs')
    linked={str(Path(k.replace('PROJECT',str(ROOT))).resolve() if Path(k.replace('PROJECT',str(ROOT))).is_absolute() else (ROOT/k).resolve()):v for k,v in engine['replacementObjects'].items()}
    for name,digest in cpu['objects'].items():require(linked.get(str((ROOT/name).resolve()))==digest,'Linked SH4 object differs: '+name)
    symbols={}
    for line in subprocess.check_output(['nm','-an',str(library)],text=True).splitlines():
        parts=line.split()
        if len(parts)>=3 and re.fullmatch('[0-9a-fA-F]+',parts[0]):symbols[parts[2]]=int(parts[0],16)
    raw=library.read_bytes();require(len(raw)>=32,'Truncated native library')
    magic,cpu_type,subtype,file_type,ncmds,size,flags,reserved=struct.unpack_from('<8I',raw)
    require(magic==0xfeedfacf and cpu_type==0x0100000c,'Expected thin arm64 Mach-O library')
    position=32;segments=[]
    for _ in range(ncmds):
        require(position+8<=len(raw),'Truncated Mach-O command');cmd,command_size=struct.unpack_from('<II',raw,position)
        require(command_size>=8 and position+command_size<=len(raw),'Invalid Mach-O command length')
        if cmd==0x19:
            require(command_size>=72,'Truncated segment command');segments.append(struct.unpack_from('<4Q',raw,position+24))
        position+=command_size
    images={r['name']:r for r in generation['images']};results={}
    layout=None;layout_entries={};layout_checked=set();layout_result=None
    if any(field in run for field in ('linkLayout','linkOrderFile','linkResolutionFile')):
        require(all(field in run for field in ('linkLayout','linkOrderFile','linkResolutionFile')),'Incomplete canonical link-layout provenance')
        layout=run['linkLayout'];order_path=ROOT/run['linkOrderFile'];resolution_path=ROOT/run['linkResolutionFile']
        require(sha(order_path)==layout['orderFileSHA256'] and sha(resolution_path)==layout['resolutionFileSHA256'],'Link-layout output changed')
        resolution=json.loads(resolution_path.read_text());configuration=ROOT/'Configuration/sh4-blocks.json';policy=json.loads(configuration.read_text())['linkLayout']
        require(layout['functionCount']==256 and layout['traceRequiredForGeneration'] is False and layout['configurationKey']=='linkLayout','Unexpected layout scope')
        require(resolution['schema']==1 and resolution['functionCount']==256 and resolution['configurationSHA256']==sha(configuration),'Layout configuration binding differs')
        require(resolution['traceRequiredForGeneration'] is False and resolution['resolvedFromCurrentAuthenticatedMediaAndTargetPlan'] is True,'Layout requires unreviewed inputs')
        priorities=policy['priorities'];entries=resolution['entries']
        require(len(entries)==len(priorities)==256 and policy['maximumFunctions']==256 and policy['traceRequiredForGeneration'] is False,'Invalid layout priority count')
        require([{'image':r['image'],'pc':r['pc']} for r in entries]==priorities,'Resolved layout PCs differ from configured priorities')
        for record in entries:
            image=record['image'];pc=int(record['pc'],16);key=(image,pc)
            require(image in images and key not in layout_entries and pc%2==0,'Duplicate or invalid resolved layout PC')
            require(record['sourceOffset']==pc-int(images[image]['base'],16) and 0<=record['sourceOffset']<=images[image]['bytes']-2,'Resolved layout PC falls outside its image')
            layout_entries[key]=record
        require(resolution['imageSHA256']=={image:images[image]['sha256'] for image in sorted({r['image'] for r in entries})},'Layout image provenance differs')
        order=[r['symbol'] for r in entries]
        require(len(set(order))==256 and order_path.read_text()==''.join(s+'\n' for s in order),'Link order does not match unique resolved targets')
        expected_argument='-Wl,-order_file,'+str(order_path.resolve())
        order_arguments=[arg.replace('PROJECT',str(ROOT)) for arg in engine['linkCommand'] if 'order_file' in arg]
        require(order_arguments==[expected_argument],'Engine link did not use exactly the authenticated order file')
    for image,table,base,plan_image in [('game0','_vt_game_targets',0x0c020000,'game0'),('bios','_vt_bios_targets',0,'bios_ram')]:
        require(table in symbols,'Missing linked immutable table '+table);address=symbols[table]
        packed_source=generated/f'vt_image_{image}.cpp';require(sha(packed_source)==generation['generated'][packed_source.name],'Fixed image identity changed')
        packed=[int(v,16) for v in re.findall(r'0x([0-9a-f]{8})',packed_source.read_text())]
        require(len(packed)*2==images[image]['bytes'] and int(images[image]['base'],16)==base,'Unexpected image/table size')
        offsets=[fileoff+address-vmaddr for vmaddr,vmsize,fileoff,filesize in segments if vmaddr<=address and address+8*len(packed)<=vmaddr+filesize]
        require(len(offsets)==1,'Table is not wholly backed by one file segment');offset=offsets[0]
        require(offset+8*len(packed)<=len(raw),'Truncated immutable table')
        blocks={(int(r['pc'],16) if image=='game0' else r['romOffset']):r for r in plan['blocks'] if r['image']==plan_image}
        overrides={}
        if image=='game0':
            overrides={int(r['pc'],16):r for r in plan.get('runEntryOverrides',[])}
            overrides.update({int(r['pc'],16):r for r in plan.get('chainEntryOverrides',[])})
        for index,value in enumerate(packed):
            word,delta=struct.unpack_from('<Ii',raw,offset+8*index);pc=base+2*index
            name=(overrides[pc]['functionName'] if pc in overrides else blocks[pc]['functionName'] if pc in blocks else 'vt_operation_'+str(value>>16))
            symbol='__Z'+str(len(name))+name+'P10Sh4ContextR9Sh4Cycles'
            require(symbol in symbols and word==value&0xffff and address+delta==symbols[symbol],f'Wrong immutable target/word at {pc:08x}')
            if (image,pc) in layout_entries:
                selected=layout_entries[(image,pc)]
                require(selected['functionName']==name and selected['symbol']==symbol,'Layout resolved a different function from the actual immutable target plan')
                layout_checked.add((image,pc))
        results[image]={'entries':len(packed),'blockTargets':len(blocks),'entryOverrides':len(overrides),'allWordsAndNativeTargetsMatch':True}
    if layout is not None:
        require(layout_checked==set(layout_entries),'Some layout priorities were not covered by authenticated tables')
        addresses=[symbols[row['symbol']] for row in resolution['entries']]
        require(all(a<b for a,b in zip(addresses,addresses[1:])),'Linked native functions do not follow the configured priority sequence')
        layout_result={'functionCount':len(addresses),'allPrioritiesResolveToExactROMDerivedTargets':True,'exactLinkedPrioritySequenceVerified':True,'orderFileSHA256':sha(order_path),'resolutionFileSHA256':sha(resolution_path)}
    verify_snapshot(ROOT,audit['inputFiles']);require(sha(library)==library_hash,'Library changed during verification');require(sha(engine_path)==engine_hash,'Link manifest changed during verification')
    total=sum(r['entries'] for r in results.values());require(total==3145728,'Unexpected total immutable coverage')
    report={'passed':True,'immutablePCEntriesChecked':total,'tables':results,'linkLayout':layout_result,'engineSHA256':library_hash,'linkManifestSHA256':engine_hash,'cpuManifestSHA256':sha(cpu_path),'generationManifestSHA256':sha(generation_path),'planSHA256':sha(plan_path),'checkerSHA256':sha(__file__),'checks':['Every expected word matches authenticated generated ROM-PC metadata.','Every relative target resolves to its exact linked fixed operation/block/override symbol.','All canonical generation, helper, configuration, source and object inputs are revalidated before and after the check.'],'runtimeCoverageClaim':False}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({'passed':True,'entries':total,'report':str(output)}))
if __name__=='__main__':main()
