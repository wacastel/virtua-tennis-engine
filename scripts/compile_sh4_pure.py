"""ROM-rooted prevalidation for sixteen reviewed pure-register SH4 segments.

Call emit() after compile_sh4_blocks.emit(), before
final generated-file hashing. No trace, profile, candidate or build recipe is
an input. Original operations, exception checks and individual cycle charges
remain intact; only unobservable plain-RAM fetches are prevalidated together.
"""
from __future__ import annotations
import hashlib,json,re,struct
from pathlib import Path

UNITS=('MT','EX','BR','LS','FE','CO')
ALLOWED_CALLS={'if','switch','GetN','GetM','GetSImm8','GetImm8','dr_hex','xd_hex','getDRn','getDRm','setDRn','CHECK_FPU_32','fixNaN64','fr_hex','isnan','sqrtf','sqrt'}
REJECT_BODY=re.compile(r'ReadMem|WriteMem|MemPtr|ctx->pc\s*(?:[+*/|&^\-]?=)|branch_target_|executeDelaySlot|Update|Exception|debugger|UTLB|CCN_|ocache|icache|iNimp|restoreHost|rounding|sq_buffer|doSqWrite|sh4_sched|CpuRunning|Sh4Interpreter')
SCOPE={'firstFetchRemainsOriginal':True,'noHoistingAcrossMemoryOrControl':True,'originalOperationBodiesAndPerOperationCyclesRetained':True,'failedPreflightHasNoArchitecturalEffects':True,'ordinaryBusEventCountsPreserved':False,'budgetStrictlyGreaterThanAllUnitMaximum':True,'plainOriginalMappedRAMOnly':True}
CYCLE_ADMISSION='''// Pure register segment: read-only admission; ordinary per-operation charges remain.
    __attribute__((always_inline)) bool vtPureSegmentReady(const Sh4Context* expected,unsigned maximum) const {
        return ctx==expected && cpuRatio==1 && ctx->cycle_counter>static_cast<int>(maximum);
    }

'''

def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):return digest(Path(path).read_bytes())
def require(condition,message):
    if not condition:raise ValueError(message)

def all_unit_costs(words,metadata):
    """Exact pinned non-memory countCycles recurrence at the sole admitted ratio1."""
    rows={}
    for initial in UNITS:
        cost=0;last=initial;prefix=[]
        for word in words:
            md=metadata[word];unit=md['unit'];issue=md['issue']
            require(md['memory'] is False and unit in UNITS and type(issue)is int and issue>=0,'Impure or invalid cycle metadata')
            if last=='CO' or unit=='CO' or (last==unit and last!='MT'):
                last=unit;cost+=issue
            else:last='CO'
            prefix.append(cost)
        require(all(a<=b for a,b in zip(prefix,prefix[1:])),'Nonmonotone cycle charges')
        rows[initial]={'cycles':cost,'finalUnit':last,'prefixCycles':prefix}
    return rows

def validate(root,source,config,metadata,images,plan):
    """Pure validation: authenticate immutable sources, word spans and every body."""
    from compile_sh4 import brace_end
    root,source=Path(root),Path(source)
    require(config['schema']==1 and config['optimizationOnly'] is True and config['traceRequiredForGeneration'] is False,'Unreviewed pure-segment schema')
    require((config['maximumSegments'],config['minimumInstructions'],config['maximumInstructions'])==(16,4,16),'Pure-segment bounds changed')
    require(config['scope']==SCOPE and set(config['allowedCalls'])==ALLOWED_CALLS,'Pure-segment admission scope changed')
    require(len(config['segments'])==16,'The sixteen reviewed spans are required')
    pins={}
    for name,h in config['originalSourceSHA256'].items():
        p=Path(name);require(not p.is_absolute() and '..'not in p.parts,'Unsafe original-source path')
        file=source/p;require(sha(file)==h,'Pinned original source changed: '+name);pins[str(file.relative_to(root))]=h
    expected={'core/hw/sh4/interpr/sh4_opcodes.cpp','core/hw/sh4/interpr/sh4_fpu.cpp','core/hw/sh4/sh4_opcode_list.cpp','core/hw/sh4/sh4_cycles.cpp','core/hw/sh4/sh4_cycles.h','core/hw/sh4/sh4_if.h','core/hw/sh4/sh4_core.h','core/hw/sh4/dyna/decoder.h'}
    require(set(config['originalSourceSHA256'])==expected,'Incomplete original-source proof')
    bodies={}
    for name in ('sh4_opcodes.cpp','sh4_fpu.cpp'):
        text=(source/'core/hw/sh4/interpr'/name).read_text()
        for match in re.finditer(r'sh4op\((\w+)\)',text):
            start=text.index('{',match.end());bodies[match[1]]=text[start:brace_end(text,start)]
    for handler,h in config['reviewedOperationBodySHA256'].items():
        body=bodies.get(handler,'');require(digest(body.encode())==h,'Reviewed original handler changed: '+handler)
        clean=re.sub(r'/\*.*?\*/|//[^\n]*','',body,flags=re.S)
        calls=set(re.findall(r'\b([a-zA-Z_]\w*)\s*\(',clean))
        require(not REJECT_BODY.search(clean) and calls<=ALLOWED_CALLS,'Original handler is outside pure scope: '+handler)
    by_image={n:(b,d)for n,b,d in images};blocks={b['pc']:b for b in plan['blocks']};seen=set();used=set();rows=[]
    for ident,record in enumerate(config['segments']):
        image=record['image'];require(image=='game0' and image in by_image,'Only the reviewed game RAM image is admitted')
        pc=int(record['pc'],16);offset=record['sourceOffset'];count=record['instructions'];first=record['firstInstructionIndex'];base,data=by_image[image]
        require(type(offset)is int and type(count)is int and type(first)is int and offset>=0 and offset%2==0 and 4<=count<=16 and first>=0,'Invalid pure span')
        require(pc==base+offset and offset+2*count<=len(data) and pc not in seen,'Unmapped or duplicate pure span');seen.add(pc)
        require(record['parentBlockPC']in blocks,'Pure segment has no original parent block');block=blocks[record['parentBlockPC']]
        require(block['image']==image and first+count<=block['instructions'] and int(block['pc'],16)+2*first==pc and block['sourceOffset']+2*first==offset,'Pure segment crosses its parent block')
        require(not block.get('repeatExactSelfLoop') and block['pc']not in {n['pc']for n in plan['guardedChain']['nodes']},'Pure batching may not modify an existing contraction')
        raw=data[offset:offset+2*count];require(digest(raw)==record['instructionSpanSHA256'],'Original pure instruction span changed')
        words=struct.unpack('<'+'H'*count,raw)
        for word in words:
            md=metadata[word];require(not md['memory'] and md['handler']in config['reviewedOperationBodySHA256'],'Unreviewed operation metadata');used.add(md['handler'])
        table=all_unit_costs(words,metadata);simple={n:{k:v for k,v in row.items()if k!='prefixCycles'}for n,row in table.items()};maximum=max(row['cycles']for row in table.values())
        require(simple==record['cycleTable'] and maximum==record['maximumExactCost'],'Offline all-unit cycle proof changed')
        # Charges are nonnegative. A budget strictly above this whole-segment
        # maximum is therefore positive at every pre-final instruction boundary.
        require(0<=maximum<=sum(metadata[w]['issue']for w in words),'Invalid cycle upper bound')
        rows.append(record|{'id':ident,'words':words,'raw':raw,'block':block,'allUnitPrefixCycles':table})
    require(used==set(config['reviewedOperationBodySHA256']),'Reviewed handler inventory is not exact')
    return rows,pins

def emit(root,source,output,metadata,images,run_optimization):
    """Update already-emitted block TUs in place; return the augmented role map."""
    from compile_sh4 import brace_end
    root,source,output=map(Path,(root,source,output))
    cp=root/'Configuration/sh4-pure-segments.json';template=root/'Sources/Translated/sh4/templates/pure_preflight.h.in'
    pins={str(cp.relative_to(root)):sha(cp),str(template.relative_to(root)):sha(template),str(Path(__file__).resolve().relative_to(root.resolve())):sha(__file__)}
    config=json.loads(cp.read_text());plan_path=output/run_optimization['plan'];plan=json.loads(plan_path.read_text())
    require('pureSegments'not in plan,'Pure segments already emitted')
    rows,original_pins=validate(root,source,config,metadata,images,plan);pins.update(original_pins)
    header=template.read_text();require('vt_try_pure_'not in header and header.count('vtPureSegmentReady')==1,'Invalid pure preflight template')
    # Preserve the same class definition in ordinary and block TUs: add this
    # layout-neutral read-only method to both overlays. Their only existing
    # difference remains the reviewed executeFixed always_inline annotation.
    cycle_before={};cycle_after={};anchor='// Exact native counter-loop boundary;'
    for directory in ('overlay','block-overlay'):
        cycle_path=output/directory/'core/hw/sh4/sh4_cycles.h';before=cycle_path.read_text()
        require(before.count(anchor)==1 and 'vtPureSegmentReady'not in before,'Cycle overlay anchor changed')
        after=before.replace(anchor,CYCLE_ADMISSION+anchor);require(after.replace(CYCLE_ADMISSION,'')==before,'Cycle overlay inverse failed')
        cycle_before[cycle_path]=before;cycle_after[cycle_path]=after
    ordinary=cycle_after[output/'overlay/core/hw/sh4/sh4_cycles.h']
    block=cycle_after[output/'block-overlay/core/hw/sh4/sh4_cycles.h']
    require(block.replace('__attribute__((always_inline)) void executeFixed()','void executeFixed()')==ordinary,'Cycle overlay class definitions differ')
    original_text={};new_text={};helper_text={};transforms=[];records=[];floating=(output/'vt_floating_templates.h').read_text();by_image={n:(b,d)for n,b,d in images}
    for row in rows:
        ident=row['id'];pc=int(row['pc'],16);words=row['words'];raw=row['raw'];first=row['firstInstructionIndex'];count=row['instructions'];block=row['block'];fn=f'vt_try_pure_{ident:02d}'
        # A retained hidden weak inline definition is the actual production
        # helper. Independent fixtures can call it without probe-only wrappers.
        helper=f'__attribute__((always_inline,used,visibility("hidden"))) inline bool {fn}(Sh4Context* ctx,Sh4Cycles& cycles) {{\n const u8* code;\n if(!vt_pure::preflight(ctx,cycles,0x{pc:08x}u,{len(raw)}u,{row["maximumExactCost"]}u,code)) return false;\n'
        offset=0;grouped=[]
        while offset<len(raw):
            width=next(n for n in (8,4,2)if n<=len(raw)-offset);value=int.from_bytes(raw[offset:offset+width],'little');suffix='ull'if width==8 else'u'
            helper+=f' if(vt_pure::load<u{width*8}>(code+{offset}u)!=0x{value:0{width*2}x}{suffix}) return false;\n';grouped.append({'offset':offset,'bytes':width});offset+=width
        helper+=' const u32 entryPC=ctx->pc-2;\n'
        for index,word in enumerate(words):
            md=metadata[word]
            if index:helper+=f' ctx->pc=entryPC+{2*index+2}u;\n'
            if md['floating']:helper+=' if(ctx->sr.FD==1) throw SH4ThrownException(ctx->pc-2,Sh4Ex_FpuDisabled);\n'
            namespace='vt_floating'if re.search(r'\b'+re.escape(md['handler'])+r'\(Sh4Context',floating)else'vt_integer'
            helper+=f' {namespace}::{md["handler"]}<0x{word:04x}>(ctx);\n cycles.executeFixed<{md["unit"]},{md["issue"]},false>();\n'
        helper+=' return true;\n}\n'
        marker=re.compile(r'void vt_block_'+f'{int(block["pc"],16):08x}'+r'\(Sh4Context \*ctx,\s*Sh4Cycles &cycles\)')
        paths=[p for p in output.glob('*blocks_*.cpp')if marker.search(p.read_text())];require(len(paths)==1,'Cannot identify unique original block TU')
        path=paths[0];name=path.name;original_text.setdefault(name,path.read_text());text=new_text.get(name,original_text[name]);match=marker.search(text);start=text.index('{',match.end());end=brace_end(text,start);body=text[start:end]
        _,data=by_image[block['image']];allwords=struct.unpack_from('<'+'H'*block['instructions'],data,block['sourceOffset']);starts=[];ends=[];position=0
        for word in allwords:
            md=metadata[word];pattern=re.compile(r'    (?:vt_integer|vt_floating)::'+re.escape(md['handler'])+rf'<0x{word:04x}>\(ctx\);');op=pattern.search(body,position);require(op is not None,'Original block operation changed')
            opstart=op.start();fd='    if(ctx->sr.FD == 1) throw SH4ThrownException(ctx->pc-2,Sh4Ex_FpuDisabled);\n'
            if md['floating']:require(body[:opstart].endswith(fd),'Original FD check changed');opstart-=len(fd)
            cycle=re.search(r'    cycles\.executeFixed<[^>]+>\(\);\n',body[op.end():]);require(cycle is not None,'Original block charge absent')
            charge=cycle.group().strip();require(charge==f'cycles.executeFixed<{md["unit"]},{md["issue"]},{str(md["memory"]).lower()}>();','Original block charge changed')
            opend=op.end()+cycle.end();starts.append(opstart);ends.append(opend);position=opend
        before=body[starts[first]:ends[first+count-1]];after=f'    if (!{fn}(ctx,cycles)) {{\n'+before+'    }\n';transforms.append((name,before,after))
        text=text[:start]+body[:starts[first]]+after+body[ends[first+count-1]:]+text[end:]
        helper_name='pure_segments_'+path.stem+'.h'
        helper_text[helper_name]=helper_text.get(helper_name,'#pragma once\n#include "pure_segments.h"\n')+helper
        if name not in new_text:text='#include "'+helper_name+'"\n'+text
        new_text[name]=text
        records.append({k:v for k,v in row.items()if k not in ('words','raw','block')}|{'function':fn,'actualBlockTranslationUnit':name,'groupedWordGuards':grouped})
    for name,text in new_text.items():
        inverse=text.removeprefix('#include "pure_segments_'+Path(name).stem+'.h"\n')
        for current,before,after in reversed(transforms):
            if current==name:require(inverse.count(after)==1,'Ambiguous caller inverse');inverse=inverse.replace(after,before)
        require(inverse==original_text[name],'Pure caller inverse failed: '+name)
        require(run_optimization['translationUnitRoles'].get(name)=='block','Pure caller has wrong compile-role overlay')
    info=SCOPE|{'segments':records,'allUnitCycleProof':True,'maximumSegments':16,'configurationSHA256':sha(cp),'helperSymbolVisibility':'hidden retained inline definitions','cycleClassLayoutUnchanged':True,'inverseCallerTransformationPassed':True,'changedTranslationUnits':sorted(new_text),'helperHeaders':sorted(helper_text),'reviewedOperationBodySHA256':config['reviewedOperationBodySHA256']}
    # Publish only after all validation and inverse checks succeed. The outer
    # generator also uses a fresh temporary output tree and hashes every file.
    for name,text in (new_text|helper_text).items():(output/name).write_text(text)
    (output/'pure_segments.h').write_text(header)
    for path,text in cycle_after.items():path.write_text(text)
    plan['pureSegments']=info;plan_path.write_text(json.dumps(plan,indent=2)+'\n')
    for name,h in pins.items():require(sha(root/name)==h,'Input changed during pure emission: '+name)
    result=dict(run_optimization);result.update(pureSegmentsHeader='pure_segments.h',pureSegments=info)
    result['generatorSources']=dict(result['generatorSources'])|{str(Path(__file__).resolve().relative_to(root.resolve())):sha(__file__)}
    result['templateSources']=dict(result['templateSources'])|{str(template.relative_to(root)):sha(template)}
    result['configurationSources']=dict(result.get('configurationSources',{}))|{str(cp.relative_to(root)):sha(cp)}
    result['pureSourcePins']=pins
    return result
