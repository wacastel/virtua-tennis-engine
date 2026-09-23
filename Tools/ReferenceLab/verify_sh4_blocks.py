#!/usr/bin/env python3
"""Isolated differential fixtures for the actual Run-only fixed SH4 blocks.

Never loads media into a running game or edits original sources. The original
interpreter remains in this laboratory dylib only, as the independent oracle.
"""
from pathlib import Path
import argparse, ctypes, hashlib, json, re, shutil, struct, subprocess, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from compile_sh4 import metadata,media_files,images,brace_end
from build_observer import compile_command,link_command,SOURCE,REFERENCE
from sh4_fixture_inputs import add_fixture_arguments,fixture_inputs
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def local(p):return str(Path(p).resolve().relative_to(ROOT))
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    add_fixture_arguments(ap,ROOT)
    args=ap.parse_args();inputs=fixture_inputs(ROOT,args);out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    candidate=inputs.include_directory;candidate_manifest=inputs.cpu_manifest if args.candidate is None else candidate/'manifest.json';cm=inputs.normalized
    mapped_fetch=cm.get('mappedFetch');replacement_objects={}
    if mapped_fetch:
        replacement_objects[mapped_fetch['originalObjectTarget']]=candidate/mapped_fetch['replacementObject']
    cpu_manifest=inputs.cpu_manifest;cpu=inputs.cpu
    if sha(cpu_manifest)!=cm['parentCPUManifestSHA256']:raise RuntimeError('Different fixed-operation parent')
    pins=dict(inputs.input_pins);pins.update({local(candidate_manifest):sha(candidate_manifest),local(cpu_manifest):sha(cpu_manifest),
          local(Path(__file__)):sha(__file__),local(Path(__file__).with_name('sh4_block_fixture.cpp')):sha(Path(__file__).with_name('sh4_block_fixture.cpp')),
          local(REFERENCE/'flycast_libretro.dylib'):sha(REFERENCE/'flycast_libretro.dylib')})
    for name,digest in cm['sources'].items():
        p=candidate/name
        if sha(p)!=digest:raise RuntimeError('Candidate source changed: '+name)
        pins[local(p)]=digest
    objects=[]
    for name,digest in cm['objects'].items():
        p=candidate/name
        if sha(p)!=digest:raise RuntimeError('Candidate object changed: '+name)
        pins[local(p)]=digest
        if p!=inputs.executor_object and (not mapped_fetch or p!=Path(mapped_fetch['replacementObject'])):objects.append(p)
    if inputs.cpu.get('runOptimization',{}).get('counterLoopSource') and not any(p.name=='counter_loop.o' for p in objects):raise RuntimeError('Actual counter-loop helper object missing from fixture link')
    for name,digest in cpu['objects'].items():
        p=ROOT/name
        if sha(p)!=digest:raise RuntimeError('Parent fixed object changed: '+name)
        if p.name.startswith(('vt_operations_','vt_image_','vt_sh4_dispatch')):objects.append(p);pins[local(p)]=digest
    plan_path=inputs.plan;plan=json.loads(plan_path.read_text());pins[local(plan_path)]=sha(plan_path)
    if sha(plan_path)!=cm['planSHA256']:raise RuntimeError('Candidate plan changed')
    files,info=media_files();all_images=images(files,info);image_by_name={n:(base,data)for n,base,data in all_images}
    plans=[]
    for block in plan['blocks']:
        plans.append(block)
        # Canonical tables always bind each authenticated BIOS block at both
        # its original ROM address and its exact initial RAM-copy address.
        # This required coverage cannot depend on optional prototype metadata.
        dual_bios = block['image']=='bios_ram' if args.candidate is None else block.get('additionalROMBinding',False)
        if dual_bios:
            ram_base,ram_data=image_by_name['bios_ram'];rom_base,rom_data=image_by_name['bios']
            ram_offset=block['sourceOffset'];rom_offset=block['romOffset'];size=2*block['instructions']
            if (block['image']!='bios_ram' or rom_base!=0 or ram_base!=0x0c000100
                or type(ram_offset)is not int or type(rom_offset)is not int
                or ram_offset<0 or ram_offset+0x100!=rom_offset
                or int(block['pc'],16)!=ram_base+ram_offset
                or ram_offset+size>len(ram_data) or rom_offset+size>len(rom_data)
                or ram_data[ram_offset:ram_offset+size]!=rom_data[rom_offset:rom_offset+size]
                or hashlib.sha256(rom_data[rom_offset:rom_offset+size]).hexdigest()!=block['sha256']):
                raise RuntimeError('Invalid authenticated dual BIOS binding')
            plans.append({**block,'pc':hex(rom_offset),'image':'bios','sourceOffset':rom_offset,'fixtureROMBinding':True})
    for block in plan.get('runEntryOverrides',[]):
        plans.append({**block,'fixtureRunOverride':True})
    table=metadata(SOURCE)
    # One independently admitted floating instruction exercises the actual
    # delay helper's FPU-disabled adjustment; the real busy-loop slot is an
    # integer store and must not spuriously raise that exception.
    if any(b.get('terminalDelayedBranch') or b.get('terminalIndirectCall') or b.get('terminalReturn') for b in plans):
        for b in list(plans):
            base,data=image_by_name[b['image']]
            found=False
            for position in range(b['instructions']):
                offset=b['sourceOffset']+2*position;code=data[offset:offset+2]
                if table[int.from_bytes(code,'little')]['floating']:
                    plans.append({'pc':hex(base+offset),'image':b['image'],'sourceOffset':offset,
                        'instructions':1,'sha256':hashlib.sha256(code).hexdigest(),'fixtureDelayHelper':True})
                    found=True;break
            if found:break
        else:raise RuntimeError('No admitted floating delay-helper fixture')
    words=[];lines=['static const FixturePlan fixturePlans[] = {']
    for block in plans:
        pc=int(block['pc'],16);base,data=image_by_name[block['image']];offset=pc-base;count=block['instructions']
        if offset!=block['sourceOffset'] or not (count==1 and (block.get('fixtureDelayHelper') or block.get('fixtureRunOverride')) or 2<=count<=16):raise RuntimeError('Invalid block bounds')
        code=data[offset:offset+count*2]
        if hashlib.sha256(code).hexdigest()!=block['sha256']:raise RuntimeError('Block bytes changed')
        branch=block.get('terminalDelayedBranch') or block.get('terminalIndirectCall') or block.get('terminalReturn')
        if branch:
            slot=branch['delaySlot'];slot_code=data[offset+count*2:offset+count*2+2]
            if (int(branch['pc'],16)!=pc+(count-1)*2 or int(slot['pc'],16)!=pc+count*2
                or int.from_bytes(code[-2:],'little')!=branch['word'] or int.from_bytes(slot_code,'little')!=slot['word']
                or hashlib.sha256(slot_code).hexdigest()!=slot['sha256']):raise RuntimeError('Delay-slot identity differs')
            code+=slot_code
        values=struct.unpack('<'+'H'*(len(code)//2),code);words.append(values)
        control=block.get('terminalIndirectCall') or block.get('terminalReturn')
        control_pc=int(control['pc'],16)if control else 0
        target_register=(16 if control['word']==0x000b else(control['word']>>8)&15)if control else 0
        lines.append('{'+f'0x{pc:08x},{count},{len(values)},0x{control_pc:08x},{target_register},'+'{'+','.join(hex(w)for w in values)+'}},')
    lines.append('};');(out/'fixture_plans.inc').write_text('\n'.join(lines)+'\n')
    # The only candidate lifecycle difference is the inner Run callsite.
    old=(SOURCE/'core/hw/sh4/interpr/sh4_interpreter.cpp' if args.candidate is None else ROOT/'build/generated/sh4/vt_sh4_executor.cpp').read_text();new=inputs.executor_source.read_text()
    unchanged=[]
    for method in ['ReadNexOp','Step','ExecuteDelayslot','ExecuteDelayslot_RTE','Run']:
        def body(text):
            start=text.index('Sh4Interpreter::'+method+'(');start=text.index('{',start);return text[start:brace_end(text,start)]
        lhs=body(old);rhs=body(new)
        if method=='Run':
            rhs=rhs.replace('vt_run_execute(ctx,op,sh4cycles);','ExecuteOpcode(op);')
            if mapped_fetch:rhs=rhs.replace('vt_mapped_fetch(ctx)','ReadNexOp()')
        if lhs!=rhs:raise RuntimeError('Unapproved lifecycle change: '+method)
        unchanged.append(method)
    shutil.copy2(Path(__file__),out/'verifier-executed.py');source=out/'fixture.cpp'
    shutil.copy2(Path(__file__).with_name('sh4_block_fixture.cpp'),source)
    obj=out/'fixture.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',source,obj)
    cmd[1:1]=['-I'+str(out),'-I'+str(candidate),'-I'+str(ROOT/'build/generated/sh4')]
    with (out/'compile.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    objects.append(obj)
    fault=ROOT/'Sources/Bridge/vt_fixed_fault.cpp';fault_obj=out/'fault.o';cmd,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',fault,fault_obj)
    with (out/'fault.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    objects.append(fault_obj);pins[local(fault)]=sha(fault)
    exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_block_fixture_case\n_vt_block_fixture_count\n')
    # The original class remains the oracle. Its exact delay helper body also
    # serves the candidate, selecting the candidate's actual fixed dispatcher
    # solely through this test-only boundary at ExecuteOpcode.
    interpreter_relative='core/hw/sh4/interpr/sh4_interpreter.cpp'
    interpreter_original=SOURCE/interpreter_relative;pins[local(interpreter_original)]=sha(interpreter_original)
    original_text=interpreter_original.read_text()
    for method in ['ReadNexOp','ExecuteDelayslot']:
        start=original_text.index('Sh4Interpreter::'+method+'(');start=original_text.index('{',start)
        original_body=original_text[start:brace_end(original_text,start)]
        start=new.index('Sh4Interpreter::'+method+'(');start=new.index('{',start)
        if original_body!=new[start:brace_end(new,start)]:raise RuntimeError('Delay lifecycle is not unchanged')
    start=new.index('Sh4Interpreter::ExecuteOpcode(');start=new.index('{',start);fixed_body=new[start+1:brace_end(new,start)-1]
    if re.sub(r'\s+','',fixed_body)!='vt_sh4_execute(ctx,op,sh4cycles);':raise RuntimeError('Unexpected actual fixed delay dispatcher')
    marker='void Sh4Interpreter::ExecuteOpcode(u16 op)\n{'
    if original_text.count(marker)!=1:raise RuntimeError('Original dispatcher marker differs')
    derived=original_text.replace(marker,'#include "vt_sh4_fixed.h"\nextern bool vt_fixture_fixed_delay;\n'+marker+'\n    if(vt_fixture_fixed_delay) {'+fixed_body+' return; }',1)
    delay_source=out/'delay-interpreter.cpp';delay_source.write_text(derived);delay_obj=out/'delay-interpreter.o'
    cmd,target=compile_command(interpreter_relative,delay_source,delay_obj);cmd[1:1]=['-I'+str(ROOT/'build/generated/sh4')]
    with (out/'delay-compile.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    lib=out/'fixture.dylib';cmd=link_command(replacement_objects|{target:delay_obj},lib)
    cmd=[('-Wl,-exported_symbols_list,'+str(exports))if x.startswith('-Wl,-exported_symbols_list,')else x for x in cmd]+[str(p)for p in objects]
    with (out/'link.log').open('w')as log:subprocess.run(cmd,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
    dll=ctypes.CDLL(str(lib));fn=dll.vt_block_fixture_case;fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p,ctypes.c_uint];fn.restype=ctypes.c_int
    if dll.vt_block_fixture_count()!=len(plans):raise RuntimeError('Incorrect linked block count')
    output=ctypes.create_string_buffer(1048576);counts={};exceptions={};guard_cases=0;terminal_outcomes={'taken':0,'notTaken':0};delayed_outcomes={};repeated_nondelay_outcomes={}
    def run(params,implementation):
        p=(ctypes.c_uint32*11)(*[x&0xffffffff for x in [*params,implementation]])
        n=fn(p,output,len(output))
        if n<=0:raise RuntimeError('Fixture output failure: '+str(n))
        return ctypes.string_at(output,n)
    def compare(category,params):
        nonlocal exceptions
        left=run(params,0);right=run(params,1)
        if left!=right:
            (out/'mismatch-original.bin').write_bytes(left);(out/'mismatch-fixed.bin').write_bytes(right)
            (out/'mismatch.json').write_text(json.dumps({'category':category,'parameters':params,'originalHeader':struct.unpack('<9I',left[:36]),'fixedHeader':struct.unpack('<9I',right[:36]),'firstByte':next((i for i,(a,b)in enumerate(zip(left,right))if a!=b),None)},indent=2)+'\n')
            raise RuntimeError('Block mismatch: '+category+' '+str(params))
        counts[category]=counts.get(category,0)+1
        ending,epc,code=struct.unpack('<3I',left[:12])
        if ending==2:exceptions[str(code)]=exceptions.get(str(code),0)+1
        if ending in (3,4):raise RuntimeError('Unexpected guard fault in valid case')
        return left
    aliases=[0,0x80000000,0xa0000000]
    for index,block in enumerate(plans):
        if block.get('fixtureDelayHelper'):
            for budget in [-3,0,1,2,7,1000]:
                for flags in [2048,2050,2049,2051]:
                    for alias in aliases:
                        result=compare('actual_delay_helper_FPU_and_budgets',[index,0,budget,0,alias,flags,-1,0,-1,-1])
                        if flags&2:
                            ending,epc,code=struct.unpack('<3I',result[:12])
                            if (ending,epc,code)!=(2,(int(block['pc'],16)|alias)-2,0x820):raise RuntimeError('FPU slot exception was not adjusted')
            for fault in [0,-1]:compare('actual_delay_helper_fetch_fault',[index,0,1,0,0,2048,-1,0,fault,-1])
            continue
        for seed in range(4):
            for budget in [-3,0,1,2,3,7,16,1000]:
                for prior in [0,5]:
                    for alias in aliases:compare('budgets_pairing_aliases',[index,seed,budget,prior,alias,seed,-1,0,-1,-1])
        for flags in [4,5,6,7,8,12]:compare('bus_wait_and_pc_change',[index,1,1000,3,0,flags,-1,0,-1,-1])
        for position in range(len(words[index])):compare('fetch_exception',[index,0,1000,1,0,0,-1,0,position,-1])
        for access in range(4):compare('data_exception',[index,0,1000,1,0,0,-1,0,-1,access])
        compare('odd_pc_original_exception',[index,0,1000,0,1,0,-1,0,-1,-1])
        if block.get('terminalNonDelayBranch'):
            terminal=words[index][-1]
            if terminal&0xff00 not in (0x8900,0x8b00):raise RuntimeError('Unapproved terminal branch')
            for condition in [16,32]:
                params=[index,0,1000,0,0,condition|64,-1,0,-1,-1]
                baseline=run(params,0);header=struct.unpack('<11I',baseline[:44])
                if header[0]!=1 or header[6]!=block['instructions']+1:raise RuntimeError('Terminal fixture did not reach the first branch')
                target=header[10]-2;branch_pc=int(block['pc'],16)+(block['instructions']-1)*2
                taken=(condition==32)==(terminal&0xff00==0x8900)
                displacement=terminal&255;displacement=displacement-256 if displacement&128 else displacement
                expected=(branch_pc+4+displacement*2 if taken else branch_pc+2)&0xffffffff
                if target!=expected:raise RuntimeError('Original terminal outcome not as prepared')
                terminal_outcomes['taken' if taken else 'notTaken']+=1
                consumed=1000-ctypes.c_int32(header[9]).value
                for budget in sorted({-1,0,1,consumed-1,consumed,consumed+1}):
                    for alias in aliases:compare('terminal_T_and_budget_boundary',[index,0,budget,0,alias,condition|64,-1,0,-1,-1])
            if block.get('repeatExactSelfLoop'):
                if int(block['pc'],16)!=0x0c0a3f9a or words[index]!=(0x62d2,0x2228,0x8bfc):raise RuntimeError('Unexpected non-delay repeat loop')
                for value_flag,name in [(128,'notTaken'),(256,'taken')]:
                    result=compare('nondelay_repeat_bus_outcome',[index,0,1000,0,0,value_flag|64,-1,0,-1,-1])
                    header=struct.unpack('<11I',result[:44]);target=header[10]-2
                    expected=int(block['pc'],16)+(6 if name=='notTaken' else 0)
                    if header[0]!=1 or header[6]!=4 or target!=expected:raise RuntimeError('Original non-delay bus outcome differs')
                    consumed=1000-ctypes.c_int32(header[9]).value
                    repeated_nondelay_outcomes[name]={'target':hex(target),'consumedCycles':consumed}
                    budgets={-3,0,1,1000}
                    for iteration in [1,2,7]:budgets.update({consumed*iteration-1,consumed*iteration,consumed*iteration+1})
                    for budget in sorted(budgets):
                        for alias in aliases:
                            for extra in [0,1,2,3,4]:compare('nondelay_repeat_budget_alias_bus_wait',[index,0,budget,0,alias,value_flag|8192|extra,-1,0,-1,-1])
                changed=compare('nondelay_repeat_bus_change_exit',[index,0,1000,0,0,16384,-1,0,-1,-1])
                header=struct.unpack('<11I',changed[:44])
                if header[0]!=1 or header[6]!=16 or header[10]-2!=int(block['pc'],16)+6:raise RuntimeError('Non-delay repeated loop did not observe changed fifth bus read')
                for iteration in [1,2,7]:
                    for position in range(3):compare('nondelay_repeat_later_fetch_exception',[index,0,1000,0,0,256|8192,-1,0,iteration*3+position,-1])
                    compare('nondelay_repeat_later_data_exception',[index,0,1000,0,0,256|8192,-1,0,-1,iteration])
        if block.get('terminalDelayedBranch'):
            if int(block['pc'],16)!=0x0c0a53d8 or words[index]!=(0x63e2,0x2338,0x8ffc,0x2f32):raise RuntimeError('Unexpected grounded busy loop')
            for value_flag,name in [(128,'notTaken'),(256,'taken')]:
                first=compare('delayed_branch_controlled_outcome',[index,0,1000,0,0,value_flag|64,-1,0,-1,-1])
                header=struct.unpack('<11I',first[:44]);target=header[10]-2
                expected=int(block['pc'],16)+(8 if name=='notTaken' else 0)
                if header[0]!=1 or header[6]!=5 or target!=expected:raise RuntimeError('Original delayed outcome differs')
                consumed=1000-ctypes.c_int32(header[9]).value;delayed_outcomes[name]={'target':hex(target),'consumedCycles':consumed}
                for budget in sorted({-3,0,1,2,3,4,5,consumed-2,consumed-1,consumed,consumed+1,1000}):
                    for alias in aliases:
                        for extras in [0,1,2,3,4]:compare('delayed_branch_budget_slot_and_alias',[index,0,budget,0,alias,value_flag|extras,-1,0,-1,-1])
                for alias in aliases:
                    for fault_type,fetch_index,data_index,extra in [('fetch',3,-1,0),('write',-1,1,0),('address',-1,-1,512)]:
                        result=compare('delayed_slot_'+fault_type+'_exception',[index,0,1000,0,alias,value_flag|extra,-1,0,fetch_index,data_index])
                        ending,epc,code=struct.unpack('<3I',result[:12])
                        expected_epc=(0x0c0a53dc if name=='taken' else 0x0c0a53de)|alias
                        if ending!=2 or epc!=expected_epc:raise RuntimeError('Delay exception EPC adjustment differs')
                    for fetch_index,data_index in [(3,-1),(-1,1)]:
                        result=compare('delayed_slot_debugger_rewind',[index,0,1000,0,alias,value_flag|32768,-1,0,fetch_index,data_index])
                        header=struct.unpack('<11I',result[:44])
                        expected_pc=(0x0c0a53de if name=='taken' else 0x0c0a53e0)|alias
                        if header[0]!=5 or header[10]!=expected_pc:raise RuntimeError('Delay debugger stop PC rewind differs')
            if block.get('repeatExactSelfLoop'):
                for budget in [-1,0,1,5,6,7,11,12,13,17,18,19,1000]:
                    for alias in aliases:
                        for bus in [256|8192,16384]:compare('repeat_budget_and_bus_change',[index,0,budget,0,alias,bus,-1,0,-1,-1])
                changed=compare('repeat_bus_change_exit',[index,0,1000,0,0,16384,-1,0,-1,-1])
                header=struct.unpack('<11I',changed[:44])
                if header[0]!=1 or header[6]!=13 or header[10]-2!=int(block['pc'],16)+8:raise RuntimeError('Repeated loop did not observe changed bus value')
                for iteration in [1,2,7]:
                    for position in range(4):
                        compare('repeat_later_fetch_exception',[index,0,1000,0,0,256|8192,-1,0,iteration*4+position,-1])
                    for operation in range(2):
                        compare('repeat_later_data_exception',[index,0,1000,0,0,256|8192,-1,0,-1,iteration*2+operation])
        control=block.get('terminalIndirectCall') or block.get('terminalReturn')
        if control:
            slot=control['delaySlot'];control_pc=int(control['pc'],16);slot_pc=int(slot['pc'],16)
            allowed={0x0c0bf336:(0x410b,0x5441),0x0c0bf33e:(0x000b,0xe000),0x0c0be080:(0x000b,0x0009),0x0c0c183a:(0x4d0b,0xe407)}
            if control_pc not in allowed or (control['word'],slot['word'])!=allowed[control_pc] or slot_pc!=control_pc+2:raise RuntimeError('Unexpected fixed call/return site')
            is_return=control['word']==0x000b;prefix='indirect_return' if is_return else 'indirect_call'
            first=compare(prefix+'_target_and_PR',[index,0,1000,0,0,262144,-1,0,-1,-1])
            header=struct.unpack('<15I',first[:60])
            expected_pr=0x0c123450 if is_return else control_pc+4
            if header[0]!=1 or header[10]-2!=0x0c123450 or header[12]!=expected_pr or header[13]!=0x0c123450:raise RuntimeError('Original call/return target or PR not reached')
            consumed=1000-ctypes.c_int32(header[9]).value
            event_offset=60+header[8];event_count=struct.unpack_from('<I',first,event_offset)[0];event_offset+=4
            data_index=0;slot_data_index=None
            for event_number in range(event_count):
                kind,address,size,pc,cycle,value=struct.unpack_from('<5IQ',first,event_offset+28*event_number)
                if kind in (1,2):
                    if pc==slot_pc+2:slot_data_index=data_index
                    data_index+=1
            if (slot['word']==0x5441)!=(slot_data_index is not None):raise RuntimeError('Original slot data-access classification differs')
            for alias in aliases:
                for budget in sorted({-3,1000,*range(consumed+2)}):
                    for extra in [0,1,2,3,4,131072]:compare(prefix+'_budget_alias_and_slot_state',[index,0,budget,0,alias,262144|extra,-1,0,-1,-1])
                mutated=compare(prefix+'_slot_mutates_target_and_PR',[index,0,1000,0,alias,262144|131072,-1,0,-1,-1])
                mh=struct.unpack('<15I',mutated[:60])
                expected_pr=0xdeadbeef if is_return else (control_pc+4)|alias
                register_ok=is_return or mh[11 if control['word']==0x410b else 14]==0x0c234560
                if mh[0]!=1 or mh[10]-2!=0x0c123450 or not register_ok or mh[12]!=expected_pr:raise RuntimeError('Call/return did not preserve the target captured before the slot')
                faults=[('fetch',block['instructions'],-1,0)]
                if slot_data_index is not None:faults.extend([('read',-1,slot_data_index,0),('address',-1,-1,524288)])
                for kind,fetch_index,data_fault,extra in faults:
                    result=compare(prefix+'_slot_'+kind+'_exception',[index,0,1000,0,alias,262144|extra,-1,0,fetch_index,data_fault])
                    ending,epc,code=struct.unpack('<3I',result[:12])
                    if ending!=2 or epc!=(control_pc|alias):raise RuntimeError('Call/return slot exception EPC not adjusted')
                stops=[(block['instructions'],-1)]
                if slot_data_index is not None:stops.append((-1,slot_data_index))
                for fetch_index,data_fault in stops:
                    result=compare(prefix+'_debugger_rewind',[index,0,1000,0,alias,262144|32768,-1,0,fetch_index,data_fault])
                    dh=struct.unpack('<11I',result[:44])
                    if dh[0]!=5 or dh[10]!=(slot_pc|alias):raise RuntimeError('Call/return debugger stop did not rewind slot PC')
    # Expected instruction words with a reader-mutated PC are a distinct
    # admission boundary, even if blindly executing that opcode would match
    # the original interpreter's arithmetic. Check every block start/interior,
    # plus first/interior fetches on later repetitions of both rooted loops.
    post_fetch_guards=0
    for index,block in enumerate(plans):
        if block.get('fixtureDelayHelper'):continue
        for position in range(block['instructions']):
            iterations=[0,1]if block.get('repeatExactSelfLoop')else[0]
            for later in iterations:
                base=256|8192 if block.get('repeatExactSelfLoop')else 262144 if block.get('terminalIndirectCall')or block.get('terminalReturn')else 0
                if later:base|=4096
                for alias in aliases:
                    result=compare('post_fetch_PC_valid_alias_later'if later else'post_fetch_PC_valid_alias',[index,0,1000,0,alias,base|1048576,position,0,-1,-1])
                    if struct.unpack_from('<I',result,len(result)-8)[0]!=1:raise RuntimeError('Post-fetch-PC fixture did not reach requested boundary')
                    rejected=run([index,0,1000,0,alias,base|2097152,position,0,-1,-1],1)
                    if struct.unpack_from('<I',rejected)[0]!=3 or struct.unpack_from('<2I',rejected,len(rejected)-8)!=(1,1):
                        raise RuntimeError('Unknown post-fetch PC was not rejected before body/data/cycle effects: '+str((index,position,later,alias)))
                    guard_cases+=1;post_fetch_guards+=1
    bodies={}
    for filename in ['sh4_opcodes.cpp','sh4_fpu.cpp']:
        text=(SOURCE/'core/hw/sh4/interpr'/filename).read_text()
        for m in re.finditer(r'sh4op\((\w+)\)',text):
            p=text.index('{',m.end());bodies[m[1]]=text[p:brace_end(text,p)]
    forbidden=re.compile(plan['rejectedBodyPattern']);overlaps=0
    for index,block in enumerate(plans):
        start=int(block['pc'],16)
        if block.get('terminalDelayedBranch') or block.get('terminalIndirectCall') or block.get('terminalReturn'):
            altered=run([index,0,1000,0,0,256|262144|65536,-1,0,-1,-1],1)
            if struct.unpack('<I',altered[:4])[0]!=3:raise RuntimeError('Changed PC during delay fetch did not fail closed')
            guard_cases+=1
            for alias in [0x40000000,0x60000000,0xe0000000]:
                invalid=run([index,0,1000,0,alias,262144,-1,0,-1,-1],1)
                if struct.unpack('<I',invalid[:4])[0]!=3:raise RuntimeError('Unsupported delayed-control alias did not fail closed')
                guard_cases+=1
        for position,word in enumerate(words[index]):
            pc=start+2*position;admitted={int.from_bytes(data[pc-base:pc-base+2],'little')for n,base,data in all_images if base<=pc<base+len(data)}
            unknown=next(v for v in range(65536)if v not in admitted)
            base_flags=2048 if block.get('fixtureDelayHelper') else 256 if block.get('terminalDelayedBranch') else 0
            fixed=run([index,0,1000,0,0,base_flags,position,unknown,-1,-1],1)
            if struct.unpack('<I',fixed[:4])[0]!=3:raise RuntimeError('Changed instruction did not fail closed')
            guard_cases+=1
            if block.get('repeatExactSelfLoop'):
                later_flags=256|8192|4096
                fixed=run([index,0,1000,0,0,later_flags,position,unknown,-1,-1],1)
                if struct.unpack('<I',fixed[:4])[0]!=3:raise RuntimeError('Later iteration instruction did not fail closed')
                guard_cases+=1
            for alternate in sorted(admitted-{word}):
                handler=table[alternate]['handler']
                if handler in bodies and not forbidden.search(bodies[handler]):
                    compare('legitimate_image_overlap',[index,0,1000,0,0,base_flags,position,alternate,-1,-1]);overlaps+=1
                    if block.get('repeatExactSelfLoop'):
                        compare('repeat_later_legitimate_overlap',[index,0,1000,0,0,256|8192|4096,position,alternate,-1,-1])
    if not overlaps:raise RuntimeError('No legitimate overlap fallback exercised')
    for alias in [0x40000000,0x60000000,0xe0000000]:
        data=run([0,0,1000,0,alias,0,-1,0,-1,-1],1)
        if struct.unpack('<I',data[:4])[0]!=3:raise RuntimeError('Unsupported PC alias did not fail closed')
        guard_cases+=1
    for path,digest in pins.items():
        if sha(ROOT/path)!=digest:raise RuntimeError('Proof input changed during fixture: '+path)
    report={'passed':True,'blocks':len(plan['blocks']),'singleInstructionRunOverrides':len(plan.get('runEntryOverrides',[])),'testedPCBindings':sum(not b.get('fixtureDelayHelper',False)for b in plans),'fixedInstructionPositions':sum(b['instructions']for b in plans if not b.get('fixtureDelayHelper')),'cases':sum(counts.values()),'caseGroups':counts,'failClosedCases':guard_cases,'postFetchPCFailClosedCases':post_fetch_guards,'observedExceptionEvents':exceptions,'terminalBranchOutcomes':terminal_outcomes,'delayedBranchOutcomes':delayed_outcomes,'repeatedNonDelayBranchOutcomes':repeated_nondelay_outcomes,
        'actualObjects':True,'actualRunEntryHeader':local(inputs.run_header),'inputMode':cm['identityKind'],'unchangedLifecycleBodies':unchanged,
        'compared':['entire prepared Sh4Context bytes','cycle counter, last execution unit, memory-pairing counter and CPU multiplier','exception PC/event before unchanged outer-loop delivery','ordered instruction-fetch/data-read/data-write events including observed PC/cycle','complete sparse backing-store writes'],
        'inputPins':pins,'fixtureLibrarySHA256':sha(lib),'fixtureSourceSHA256':sha(source),'planIncludeSHA256':sha(out/'fixture_plans.inc'),'actualDelayHelper':{'originalBodyMatchesCandidate':True,'fixedExecuteOpcodeBodyExtractedFromCandidate':True,'derivedInterpreterSHA256':sha(delay_source)},
        'limits':'Isolated inner-Run differential test on deterministic redirected buses, with SR.S=0 because the pinned original MAC.L rejects saturated arithmetic. Non-delay terminal tests supply both T values at redirected fetch. The grounded BF/S tests control the preceding load as zero/nonzero, retaining the real TST and independently fetched store; they exercise taken/untaken paths, delay exceptions and exact cycle exhaustion. The original delay lifecycle body is byte-identical to the candidate and calls its exact fixed dispatcher body in native fixture mode. A separate admitted floating instruction checks the same delay helper with FPU disabled. Ordinary budget cases execute bounded internal back-edges. Does not claim complete MMU/cache or physical-device testing, outer interrupt/exception delivery, or new Step/RTE coverage. Complete original/native replays remain required. No performance measurement.'}
    (out/'acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k]for k in ['passed','blocks','cases','caseGroups','failClosedCases']}))
if __name__=='__main__':main()
