"""Deterministic bounded SH4 Run optimization from authenticated original media.

Review-only draft. No profiler output or experimental build directory is read.
Call emit() only after the ordinary fixed generator authenticates source/media.
"""
from __future__ import annotations
import hashlib,json,re,shutil,struct
from pathlib import Path
from validate_sh4_blocks import validate,require,CHAIN_NODES,REJECT_BODY


def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):return digest(Path(path).read_bytes())
def body_at(text,marker):
    # Same pinned source parser used by the base generator.
    from compile_sh4 import brace_end
    brace=text.index('{',text.index(marker));return text[brace:brace_end(text,brace)]
def fixed_cycle(row):return f'cycles.executeFixed<{row["unit"]},{row["issue"]},{str(row["memory"]).lower()}>();'
def symbol(name):return f'__Z{len(name)}{name}P10Sh4ContextR9Sh4Cycles'


def emit_layout(root,output,images,operation_ids,run_optimization):
    """Resolve source-only PC priorities to this generation's actual targets.

    The public configuration contains no generated operation symbols or trace
    counts. This runs after optional pure-segment emission and before indexing
    generated files. It changes link layout only, never native instructions.
    """
    root,output=map(Path,(root,output))
    config_path=root/'Configuration/sh4-blocks.json';config=json.loads(config_path.read_text())
    policy=config.get('linkLayout')
    if policy is None:return run_optimization
    require(set(policy)=={'maximumFunctions','priorities','traceRequiredForGeneration'},'Unknown link-layout policy fields')
    require(policy['maximumFunctions']==256 and policy['traceRequiredForGeneration'] is False,'Unreviewed link-layout scope')
    priorities=policy['priorities'];require(isinstance(priorities,list) and len(priorities)==256,'Exactly256 reviewed layout priorities are required')
    plan=json.loads((output/run_optimization['plan']).read_text());by_image={n:(b,d)for n,b,d in images}
    lookup={}
    for row in plan['blocks']:
        if row['image']=='game0':lookup[('game0',int(row['pc'],16))]=row['functionName']
        elif row['image']=='bios_ram':lookup[('bios',row['romOffset'])]=row['functionName']
    for row in plan.get('runEntryOverrides',[])+plan.get('chainEntryOverrides',[]):
        lookup[('game0',int(row['pc'],16))]=row['functionName']
    entries=[];seen_pc=set();seen_symbols=set()
    for record in priorities:
        require(isinstance(record,dict) and set(record)=={'image','pc'},'Layout priorities must contain only original image and PC')
        image=record['image'];pc_text=record['pc']
        require(image=='game0' and image in by_image and isinstance(pc_text,str) and re.fullmatch(r'0x[0-9a-f]{8}',pc_text),'Unreviewed or malformed layout image/PC')
        pc=int(pc_text,16);base,data=by_image[image];offset=pc-base
        require(pc%2==0 and 0<=offset and offset+2<=len(data) and (image,pc)not in seen_pc,'Unmapped or duplicate layout PC')
        word=struct.unpack_from('<H',data,offset)[0]
        require(word in operation_ids,'Layout PC has no fixed operation')
        function=lookup.get((image,pc),'vt_operation_'+str(operation_ids[word]));native_symbol=symbol(function)
        require(native_symbol not in seen_symbols,'Layout PCs resolve to a duplicate native function')
        seen_pc.add((image,pc));seen_symbols.add(native_symbol)
        entries.append({'image':image,'pc':pc_text,'sourceOffset':offset,'functionName':function,'symbol':native_symbol})
    order=''.join(row['symbol']+'\n' for row in entries)
    resolution={'schema':1,'functionCount':len(entries),'configurationSHA256':sha(config_path),'configurationKey':'linkLayout','traceRequiredForGeneration':False,'resolvedFromCurrentAuthenticatedMediaAndTargetPlan':True,'imageSHA256':{name:digest(by_image[name][1]) for name in sorted({r['image'] for r in entries})},'entries':entries}
    (output/'hot-functions.order').write_text(order)
    (output/'layout-resolution.json').write_text(json.dumps(resolution,indent=2)+'\n')
    metadata={'functionCount':len(entries),'orderFileSHA256':sha(output/'hot-functions.order'),'resolutionFileSHA256':sha(output/'layout-resolution.json'),'configurationKey':'linkLayout','traceRequiredForGeneration':False}
    return dict(run_optimization,linkOrderFile='hot-functions.order',linkResolutionFile='layout-resolution.json',linkLayout=metadata)


def emit(root,source,output,metadata,images,operation_ids):
    root,source,output=map(Path,(root,source,output))
    config_path=root/'Configuration/sh4-blocks.json';config=json.loads(config_path.read_text())
    from compile_sh4 import brace_end
    original_bodies={}
    for name in ('sh4_opcodes.cpp','sh4_fpu.cpp'):
        text=(source/'core/hw/sh4/interpr'/name).read_text()
        for match in re.finditer(r'sh4op\((\w+)\)',text):
            brace=text.index('{',match.end());original_bodies[match[1]]=text[brace:brace_end(text,brace)]
    validated=validate(config,images,metadata,original_bodies)
    from compile_sh4_counter import validate as validate_counter,add_cycle_methods,emit as emit_counter
    counter=validate_counter(config['counterLoop'],images,metadata,original_bodies)
    counter_cycles=add_cycle_methods(root,output)
    blocks=validated['blocks'];delays=validated['delays'];overrides=validated['runEntryOverrides']
    by_image={n:(base,data) for n,base,data in images}
    template_root=root/'Sources/Translated/sh4/templates'
    for name in ('block_templates.h','mapped_fetch.h','run_dispatch.h'):
        shutil.copyfile(template_root/name,output/name)
    # Block-local template copies are explicit includes; ordinary operation
    # translation units always retain the untouched base generated templates.
    read32_info={}
    if config.get('mappedRead32'):
        policy=config['mappedRead32']
        require(policy['readerIdentityRequired']=='addrspace::read32' and all(policy[k] is True for k in ('tableReadOnEveryRead','handlerUsesCapturedReader','writesAndOtherWidthsUnchanged','ordinaryFixedOperationsUnchanged')) and policy['RF32Exposed'] is False,'Read32 scope changed')
        shutil.copyfile(template_root/'mapped_read32.h',output/'mapped_read32.h')
        (output/'block-templates').mkdir(exist_ok=True)
        header=output/'block_templates.h';header_text=header.read_text()
        for name,count in [('vt_integer_templates.h',policy['integerCallSites']),('vt_floating_templates.h',policy['floatingCallSites'])]:
            original=(output/name).read_text();require(original.count('ReadMem32(')==count,'Original read32 call sites changed')
            derived='#include "mapped_read32.h"\n'+original.replace('ReadMem32(','vt_mapped_read32(')
            require(derived.removeprefix('#include "mapped_read32.h"\n').replace('vt_mapped_read32(','ReadMem32(')==original,'Data template inverse transformation failed')
            (output/'block-templates'/name).write_text(derived)
            before='#include "'+name+'"';after='#include "block-templates/'+name+'"';require(header_text.count(before)==1,'Block template include changed');header_text=header_text.replace(before,after)
            read32_info[name]={'originalSHA256':digest(original.encode()),'substitutions':count,'inverseVerified':True}
        header.write_text(header_text)
    # The ordinary fixed-op objects keep their original cycle header. Only
    # block/slot/chain TUs receive the identical header plus inline annotation.
    shutil.copytree(output/'overlay',output/'block-overlay',dirs_exist_ok=True)
    cycles=output/'block-overlay/core/hw/sh4/sh4_cycles.h';text=cycles.read_text()
    old='void executeFixed()';new='__attribute__((always_inline)) void executeFixed()'
    require(text.count(old)==1,'Fixed cycle helper shape changed');cycles.write_text(text.replace(old,new))
    # One inverse-transformable visibility change, preserving original read16
    # and private handler tables. Every fetch still observes the live map.
    address_source=source/config['mappedFetch']['originalSource'];text=address_source.read_text()
    require(sha(address_source)==config['mappedFetch']['originalSourceSHA256'],'Address-space source changed')
    old='static void* memInfo_ptr[0x100];';new='__attribute__((visibility("hidden"))) void* memInfo_ptr[0x100];'
    require(text.count(old)==1,'Original memory-map declaration changed')
    derived=text.replace(old,new);require(derived.replace(new,old)==text,'Address-space inverse transformation failed')
    (output/'addrspace.cpp').write_text(derived)
    # Keep Step, ordinary delay and RTE bodies untouched.
    executor=output/'vt_sh4_executor.cpp';text=executor.read_text();start=text.index('void Sh4Interpreter::Run()');end=text.index('void Sh4Interpreter::Start()',start)
    run=text[start:end];require(run.count('u32 op = ReadNexOp();')==1 and run.count('ExecuteOpcode(op);')==1,'Original Run lifecycle changed')
    run=run.replace('u32 op = ReadNexOp();','u32 op = vt_mapped_fetch(ctx);').replace('ExecuteOpcode(op);','vt_run_execute(ctx,op,sh4cycles);')
    executor.write_text(text[:start]+'#include "run_dispatch.h"\n#include "mapped_fetch.h"\n'+run+text[end:])
    integer=(output/'vt_integer_templates.h').read_text();floating=(output/'vt_floating_templates.h').read_text()
    # All five slot helpers and branch bodies come from original templates and
    # original metadata; neither units nor register operands are hand guessed.
    helpers=['#pragma once','#include "block_templates.h"','#include "hw/sh4/sh4_core.h"','#include "debug/gdb_server.h"']
    branch_names={};delay_records={}
    for pc,site in delays.items():
        word,slot=site['word'],site['slotWord'];row,slotrow=site['metadata'],site['slotMetadata'];slotpc=pc+2
        slotname=f'vt_exact_delay_{slotpc:08x}';branch=f'vt_exact_branch_{pc:08x}';branch_names[pc]=branch
        helpers.append(f'''__attribute__((always_inline)) static inline void {slotname}(Sh4Context *ctx,Sh4Cycles &cycles) {{
    try {{
        const uint32_t instructionPC=ctx->pc;
        const uint16_t op=vt_block_fetch(ctx);
        const uint32_t alias=instructionPC&0xe0000000u;
        if ((alias==0 || alias==0x80000000u || alias==0xa0000000u)
            && (instructionPC&0x1fffffffu)==0x{slotpc:08x}u
            && ctx->pc==instructionPC+2u && op==0x{slot:04x}u) {{
            vt_integer::{slotrow['handler']}<0x{slot:04x}>(ctx);
            {fixed_cycle(slotrow)}
        }} else {{ vt_sh4_execute(ctx,op,cycles); }}
    }} catch (SH4ThrownException& ex) {{
        AdjustDelaySlotException(ex);
        throw ex;
    }} catch (const debugger::Stop& e) {{
        ctx->pc -= 2;
        throw e;
    }}
}}
''')
        body=body_at(integer,f'template<uint16_t op> static void {row["handler"]}(Sh4Context *ctx)')
        require(body.count('executeDelaySlot();')==1,'Original delayed branch helper changed')
        body=body.replace('executeDelaySlot();',f'{slotname}(ctx,cycles);')
        if site['kind']=='jsr':
            require(body.count('GetN(op)')==1,'Original JSR operand changed');body=body.replace('GetN(op)',f'{(word>>8)&15}u')
        elif site['kind']=='bf_s':
            require(body.count('branch_target_s8<op>(ctx)')==1,'Original BF/S target changed');body=body.replace('branch_target_s8<op>(ctx)',f'vt_integer::branch_target_s8<0x{word:04x}>(ctx)')
        require(not re.search(r'\bop\b',body),'A branch operand remains dynamic')
        helpers.append(f'__attribute__((always_inline)) static inline void {branch}(Sh4Context *ctx,Sh4Cycles &cycles) '+body)
        delay_records[pc]={'pc':f'0x{pc:08x}','word':word,'metadata':row,'delaySlot':{'pc':f'0x{slotpc:08x}','word':slot,'metadata':slotrow,'sha256':site['slotSHA256']},'exactDelaySpecialization':True,'originalBodySHA256':digest(original_bodies[row['handler']].encode())}
    (output/'exact_game_delays.h').write_text('\n'.join(helpers)+'\n')
    emitted_bodies={};record_by_pc={};tu_roles={};source_of={}
    def operation(word,pc):
        row=metadata[word];lines=[]
        if row['floating']:lines.append('    if(ctx->sr.FD == 1) throw SH4ThrownException(ctx->pc-2,Sh4Ex_FpuDisabled);')
        if pc in branch_names:lines.append(f'    {branch_names[pc]}(ctx,cycles);')
        else:
            ns='vt_floating' if row['handler'] in floating else 'vt_integer'
            lines.append(f'    {ns}::{row["handler"]}<0x{word:04x}>(ctx);')
        lines.append('    '+fixed_cycle(row));return lines
    for image,prefix in [('game0','blocks'),('bios_ram','bios_blocks')]:
        selected=[b for b in blocks if b['image']==image]
        for begin in range(0,len(selected),16):
            filename=f'{prefix}_{begin//16:02d}.cpp';lines=['#include "block_templates.h"','#include "exact_game_delays.h"']
            for b in selected[begin:begin+16]:
                pc=int(b['pc'],16);words=b.pop('words');b.pop('metadata');repeat=b.get('repeatExactSelfLoop',False)
                body=['{','    const uint32_t entryPC=ctx->pc-2;']
                if repeat:body.append('    for (;;) {')
                for index,word in enumerate(words):
                    current=pc+index*2
                    if index:body.extend([f'    if(ctx->cycle_counter<=0 || ctx->pc != entryPC+{index*2}u) return;','    { const uint16_t fetched=vt_block_fetch(ctx);',f'      if(ctx->pc!=entryPC+{(index+1)*2}u || fetched!=0x{word:04x}) {{ vt_sh4_execute(ctx,fetched,cycles); return; }} }}'])
                    body.extend(operation(word,current))
                terminalpc=pc+2*(len(words)-1);terminalword=words[-1]
                if terminalpc in delays:
                    kind={'bf_s':'terminalDelayedBranch','jsr':'terminalIndirectCall','rts':'terminalReturn'}[delays[terminalpc]['kind']];b[kind]=delay_records[terminalpc]
                elif terminalword&0xff00 in (0x8900,0x8b00):
                    b['terminalNonDelayBranch']=True;b['originalStraightLineInstructions']=len(words)-1
                if repeat:
                    body.extend(['    if(ctx->cycle_counter<=0 || ctx->pc != entryPC) return;','    { const uint16_t fetched=vt_block_fetch(ctx);',f'      if(ctx->pc!=entryPC+2u || fetched!=0x{words[0]:04x}) {{ vt_sh4_execute(ctx,fetched,cycles); return; }} }}','    }'])
                body.append('}');body='\n'.join(body);name=b['functionName'];emitted_bodies[name]=body;record_by_pc[pc]=b;source_of[name]=filename
                lines.append(f'void {name}(Sh4Context *ctx,Sh4Cycles &cycles) '+body)
            (output/filename).write_text('\n'.join(lines)+'\n');tu_roles[filename]='block'
    wrappers=['#include "exact_game_delays.h"']
    for b in overrides:
        pc=int(b['pc'],16);word=b.pop('word');b.pop('metadata');name=b['functionName'];row=metadata[word]
        body='{ '+f'{branch_names[pc]}(ctx,cycles); {fixed_cycle(row)}'+' }';wrappers.append(f'void {name}(Sh4Context *ctx,Sh4Cycles &cycles) '+body)
        b['terminalReturn' if word==0xb else 'terminalIndirectCall']=delay_records[pc];emitted_bodies[name]=body;record_by_pc[pc]=b;source_of[name]='run_slot_branches.cpp'
    (output/'run_slot_branches.cpp').write_text('\n'.join(wrappers)+'\n');tu_roles['run_slot_branches.cpp']='block'
    # Finite six-node graph. Copies are byte-identical brace bodies, not another
    # implementation of any original operation. First entry executes at <=0.
    chain=config['guardedChain'];require(tuple(int(n['pc'],16) for n in chain['nodes'])==CHAIN_NODES,'Chain node set changed')
    require(chain['maximumNodes']==6 and all(chain[k] is True for k in ('sameVirtualAlias','positiveBudgetBeforeSuccessorFetch','postFetchPCGuard','inlineExactBodies')),'Chain guard policy changed')
    require(config['postFetchPCGuards']=={'blockInterior':True,'repeatFirst':True,'chainPrivateBodies':True},'Post-fetch admission policy changed')
    require(chain['controlScaffold']=='direct-labels','Chain scaffold changed')
    declarations=['i0110_nnnn_mmmm_0010','i0101_nnnn_mmmm_iiii','i1101_nnnn_iiii_iiii']
    require(chain['inlineLoadDeclarations']==declarations,'Chain annotation scope changed')
    header=output/('block-templates/vt_integer_templates.h' if read32_info else 'vt_integer_templates.h')
    original=header.read_text();annotated=original
    for handler in declarations:
        before='template<uint16_t op> static void '+handler+'('
        after='template<uint16_t op> __attribute__((always_inline)) static inline void '+handler+'('
        require(annotated.count(before)==1,'Chain template declaration changed');annotated=annotated.replace(before,after)
    require(annotated.replace('__attribute__((always_inline)) static inline void ','static void ')==original,'Chain template inverse transformation failed')
    (output/'chain_integer_templates.h').write_text(annotated)
    header=(output/'block_templates.h').read_text();old='#include "'+('block-templates/' if read32_info else '')+'vt_integer_templates.h"';new='#include "chain_integer_templates.h"'
    require(header.count(old)==1,'Chain integer include changed');(output/'chain_block_templates.h').write_text(header.replace(old,new))
    header=(output/'exact_game_delays.h').read_text();require(header.count('#include "block_templates.h"')==1,'Chain delay include changed');(output/'chain_game_delays.h').write_text(header.replace('#include "block_templates.h"','#include "chain_block_templates.h"'))
    counter_info=emit_counter(root,output,counter,counter_cycles);tu_roles['counter_loop.cpp']='block'
    nodes=[];chaincode=['#include "counter_loop.h"','#include "mapped_fetch.h"','#include "chain_block_templates.h"','#include "chain_game_delays.h"']
    for index,node in enumerate(chain['nodes']):
        pc=int(node['pc'],16);require(int(node['nextPC'],16)==CHAIN_NODES[(index+1)%6],'Unapproved graph edge')
        record=record_by_pc[pc];require(node['originalFunction']==record['functionName'] and node['sha256']==record['sha256'],'Chain original node differs')
        name=record['functionName'];body=emitted_bodies[name];private=f'vt_chain_node_{pc:08x}';base,data=by_image[record['image']];word=struct.unpack_from('<H',data,record['sourceOffset'])[0]
        chaincode.append(f'__attribute__((always_inline)) static inline void {private}(Sh4Context *ctx,Sh4Cycles &cycles) '+body)
        nodes.append(dict(node,firstWord=word,privateFunction=private,bodySHA256=digest(body.encode()),sourceFile=source_of[name],sourceSHA256=sha(output/source_of[name])))
    chaincode.extend(['__attribute__((noinline)) static void vt_hot_chain(Sh4Context *ctx,Sh4Cycles &cycles,unsigned node)','{','    const uint32_t entryPC=ctx->pc-2;','    const uint32_t alias=entryPC&0xe0000000u;','    switch(node) {'])
    for index,node in enumerate(nodes):chaincode.append(f'    case {index}: goto node_{index};')
    chaincode.extend(['    default: vt_fixed_fault("SH4",ctx->pc,0,"Invalid offline chain node");','    }'])
    for index,node in enumerate(nodes):
        nxt=(index+1)%6
        chaincode.extend([f'node_{index}:','    {',f'        {node["privateFunction"]}(ctx,cycles);',f'        const uint32_t successor=alias|{node["nextPC"]}u;','        if (ctx->cycle_counter<=0 || ctx->pc!=successor) return;','        const uint32_t capturedSuccessorPC=ctx->pc;','        const uint16_t fetched=vt_mapped_fetch(ctx);',f'        if (ctx->pc!=capturedSuccessorPC+2u || fetched!=0x{nodes[nxt]["firstWord"]:04x}u) {{','            vt_sh4_execute(ctx,fetched,cycles);','            return;','        }',f'        goto node_{nxt};','    }'])
    chaincode.append('}')
    for index,node in enumerate(nodes):chaincode.append(f'void {node["entryFunction"]}(Sh4Context *ctx,Sh4Cycles &cycles) {{ vt_hot_chain(ctx,cycles,{index}); }}')
    chaintext='\n'.join(chaincode)+'\n'
    entry='node_0:\n    {\n        vt_chain_node_0c0c183a(ctx,cycles);'
    require(chaintext.count(entry)==1,'Counter-loop entry changed')
    chaintext=chaintext.replace(entry,'node_0:\n    {\n        if (vt_try_counter_loop(ctx,cycles)) return;\n        vt_chain_node_0c0c183a(ctx,cycles);')
    for node in nodes:require(digest(body_at(chaintext,'void '+node['privateFunction']+'(').encode())==node['bodySHA256'],'Inline body differs')
    (output/'guarded_chain.cpp').write_text(chaintext);tu_roles['guarded_chain.cpp']='block'
    chain_overrides=[dict(record_by_pc[int(n['pc'],16)],functionName=n['entryFunction']) for n in nodes]
    plan={'rejectedBodyPattern':REJECT_BODY.pattern,'blocks':blocks,'runEntryOverrides':overrides,'chainEntryOverrides':chain_overrides,'guardedChain':dict(chain,nodes=nodes),'counterLoop':counter_info,'maximumInstructionsPerBlock':16,'traceRequiredForGeneration':False,'configurationSHA256':sha(config_path)}
    (output/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    # Immutable PC-relative target data contains expected words and exactly one
    # preselected native function for each original image position.
    for image,symbol_name,filename in [('game0','vt_game_targets','game-targets.s'),('bios','vt_bios_targets','bios-targets.s')]:
        base,data=by_image[image];lookup={}
        for b in blocks:
            if image=='game0' and b['image']=='game0':lookup[int(b['pc'],16)]=b['functionName']
            elif image=='bios' and b['image']=='bios_ram':lookup[b['romOffset']]=b['functionName']
        if image=='game0':
            lookup.update({int(b['pc'],16):b['functionName'] for b in overrides});lookup.update({int(b['pc'],16):b['functionName'] for b in chain_overrides})
        with (output/filename).open('w') as f:
            f.write(f'.section __TEXT,__const\n.globl _{symbol_name}\n.p2align 3\n_{symbol_name}:\n')
            for index,(word,) in enumerate(struct.iter_unpack('<H',data)):
                name=lookup.get(base+2*index,'vt_operation_'+str(operation_ids[word]));f.write(f'.long 0x{word:04x}, {symbol(name)} - _{symbol_name}\n')
        tu_roles[filename]='assembly'
    tu_roles['addrspace.cpp']='address-space';tu_roles['vt_sh4_executor.cpp']='ordinary'
    return {'plan':'plan.json','executorSource':'vt_sh4_executor.cpp','runDispatchHeader':'run_dispatch.h','mappedFetchHeader':'mapped_fetch.h','derivedAddressSpaceSource':'addrspace.cpp','translationUnitRoles':tu_roles,'configurationSHA256':sha(config_path),'generatorSources':{str(p.relative_to(root)):sha(p) for p in (root/'scripts/compile_sh4_blocks.py',root/'scripts/validate_sh4_blocks.py',root/'scripts/compile_sh4_counter.py')},'templateSources':{str(p.relative_to(root)):sha(p) for p in sorted(p for p in template_root.iterdir() if p.is_file())},'mappedFetch':{'originalObjectTarget':'CMakeFiles/flycast_libretro.dir/core/hw/mem/addrspace.cpp.o','replacementObject':'addrspace.o','originalSource':config['mappedFetch']['originalSource'],'originalSourceSHA256':sha(address_source),'sourceInverseTransformVerified':True},'counterLoop':counter_info,'counterLoopHeader':'counter_loop.h','counterLoopSource':'counter_loop.cpp','originalBodyHashes':validated['originalBodyHashes'],**({'mappedRead32Header':'mapped_read32.h','mappedRead32':read32_info} if read32_info else {})}
