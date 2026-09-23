#!/usr/bin/env python3
"""Offline, address-selected SH4 execution for exact Virtua Tennis media.

The emitted PC maps select fixed native functions. Fetched instruction words
are authentication guards only; they never index an opcode decoder/table.
Original bodies, FPU classification and cycle metadata are selected here.
"""
from __future__ import annotations
import argparse, hashlib, json, re, struct, shutil, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'build/reference-source/flycast'
BASE = 'core/hw/sh4/'
MEMORY_TYPES = {2,3,5,6,7,12,17,18,19,22,23,25,27,29,31,33,35}

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Pinned SH4 source anchor changed: '+old[:90])
    return text.replace(old, new, 1)

def brace_end(text, start):
    depth = 0
    # This is used only for pinned handler/function blocks. Braces in their
    # strings/comments are balanced in the pinned source, asserted by hashes.
    for i in range(start,len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if not depth: return i+1
    raise RuntimeError('Unterminated pinned block')

def fields(text):
    result, start, nesting, quoted, escaped = [], 0, 0, False, False
    for i,c in enumerate(text):
        if quoted:
            if escaped: escaped=False
            elif c == '\\': escaped=True
            elif c == '"': quoted=False
        elif c == '"': quoted=True
        elif c == '(': nesting += 1
        elif c == ')': nesting -= 1
        elif c == ',' and nesting == 0:
            result.append(text[start:i].strip());start=i+1
    result.append(text[start:].strip())
    return result

def metadata(source):
    text = (source/(BASE+'sh4_opcode_list.cpp')).read_text()
    masks={k:int(v,0) for k,v in re.findall(r'#define (Mask_\w+) (0x[0-9A-F]+)',text)}
    start=text.index('static sh4_opcodelistentry opcodes[]=')
    start=text.index('{',start)
    body=text[start+1:brace_end(text,start)-1]
    body=re.sub(r'//[^\n]*','',body)
    rows=[]
    for raw in re.findall(r'\{([^{}]*)\}',body):
        row=fields(raw)
        if row[1]=='0':continue
        if len(row)<10:raise RuntimeError('Incomplete opcode row')
        row[3] = '0x085B' if row[3]=='REIOS_OPCODE' else row[3]
        rows.append({'handler':row[1],'mask':masks[row[2]],'value':int(row[3],0),
            'floating':row[4] in ('UsesFPU','FWritesFPSCR'),
            'issue':int(row[6]),'unit':row[8],'memory':int(row[9]) in MEMORY_TYPES})
    missing={'handler':'iNotImplemented','floating':False,'issue':0,'unit':'CO','memory':False}
    result=[missing.copy() for _ in range(65536)]
    # Original BuildOpcodeTables overwrites in declaration order.
    for row in rows:
        for word in range(65536):
            if word & row['mask'] == row['value']:
                result[word]=row
    return result

def templates(source):
    integer=(source/(BASE+'interpr/sh4_opcodes.cpp')).read_text()
    # The original dynamic decoder header is used only for these operand macros.
    decoder=(source/(BASE+'dyna/decoder.h')).read_text()
    macros='\n'.join(x for x in decoder.splitlines() if x.startswith('#define Get'))
    integer=replace_once(integer,'#include "hw/sh4/dyna/decoder.h"',macros)
    integer=replace_once(integer,'u32 branch_target_s8(Sh4Context *ctx, u32 op)',
        'template<uint16_t op> static u32 branch_target_s8(Sh4Context *ctx)')
    integer=replace_once(integer,'static u32 branch_target_s12(Sh4Context *ctx, u32 op)',
        'template<uint16_t op> static u32 branch_target_s12(Sh4Context *ctx)')
    for name in ('branch_target_s8','branch_target_s12'):
        integer=integer.replace(name+'(ctx, op)',name+'<op>(ctx)')
    integer=replace_once(integer,'//Read Mem macros','namespace vt_integer {\n//Read Mem macros')+'\n}\n'
    integer=re.sub(r'sh4op\((\w+)\)',r'template<uint16_t op> static void \1(Sh4Context *ctx)',integer)
    floating=(source/(BASE+'interpr/sh4_fpu.cpp')).read_text()
    floating=replace_once(floating,'#include "sh4_opcodes.h"','')
    floating=replace_once(floating,'static u32 GetN(u32 op) {','namespace vt_floating {\n#undef GetN\n#undef GetM\ntemplate<uint16_t op> static constexpr u32 GetN() {')
    floating=replace_once(floating,'static u32 GetM(u32 op) {','template<uint16_t op> static constexpr u32 GetM() {')
    floating=replace_once(floating,'static double getDRn(Sh4Context *ctx, u32 op) {','template<uint16_t op> static double getDRn(Sh4Context *ctx) {')
    floating=replace_once(floating,'static double getDRm(Sh4Context *ctx, u32 op) {','template<uint16_t op> static double getDRm(Sh4Context *ctx) {')
    floating=replace_once(floating,'static void setDRn(Sh4Context *ctx, u32 op, double d) {','template<uint16_t op> static void setDRn(Sh4Context *ctx, double d) {')
    for name in ('GetN','GetM'):
        floating=floating.replace(name+'(op)',name+'<op>()')
    for name in ('getDRn','getDRm'):
        floating=floating.replace(name+'(ctx, op)',name+'<op>(ctx)')
    floating=floating.replace('setDRn(ctx, op,','setDRn<op>(ctx,')
    floating=re.sub(r'sh4op\((\w+)\)',r'template<uint16_t op> static void \1(Sh4Context *ctx)',floating)+'\n}\n'
    return integer,floating

def lifecycle(source, output):
    overlay=output/'overlay'/BASE
    overlay.mkdir(parents=True,exist_ok=True)
    headers={}
    # Copy headers so quoted relative includes always resolve to the same fixed
    # class definition. Their layouts remain byte-for-byte ABI compatible.
    for path in (source/BASE).rglob('*.h'):
        target=output/'overlay'/path.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
        headers[str(path.relative_to(source))]=sha(path)
    interpreter=(overlay/'sh4_interpreter.h').read_text()
    (overlay/'sh4_interpreter.h').write_text(replace_once(interpreter,
        'static constexpr int CPU_RATIO = 8;', 'static constexpr int CPU_RATIO = 1;'))
    text=(source/(BASE+'sh4_cycles.h')).read_text()
    old='\tvoid executeCycles(u16 op)\n\t{\n\t\tctx->cycle_counter -= countCycles(op);\n\t}'
    method='''
    template<sh4_eu Unit, int Issue, bool Memory> void executeFixed()
    {
        int cycles = 0;
#ifndef STRICT_MODE
        if constexpr (Memory) {
            if (++memOps < 4) cycles = mmu_enabled() ? 5 : 2;
        }
#endif
        if (lastUnit == CO || Unit == CO || (lastUnit == Unit && lastUnit != MT)) {
            lastUnit = Unit;
            cycles += Issue;
        } else lastUnit = CO;
        ctx->cycle_counter -= cycles * cpuRatio;
    }
'''
    text=replace_once(text,old,method).replace('\tint countCycles(u16 op);','')
    # mmu_enabled is declared in modules/mmu.h, which itself includes this
    # header through ngen.h. A matching declaration avoids that include cycle.
    text=replace_once(text,'class Sh4Cycles','static inline bool mmu_enabled();\n\nclass Sh4Cycles')
    (overlay/'sh4_cycles.h').write_text(text)
    cycles=(source/(BASE+'sh4_cycles.cpp')).read_text()
    a=cycles.index('int Sh4Cycles::countCycles(');b=cycles.index('{',a)
    cycles=cycles[:a]+cycles[brace_end(cycles,b):]
    (output/'vt_sh4_cycles.cpp').write_text(cycles)
    wrapper=(source/(BASE+'interpr/sh4_interpreter.cpp')).read_text()
    a=wrapper.index('void Sh4Interpreter::ExecuteOpcode(');b=wrapper.index('{',a)
    wrapper=wrapper[:a]+'''#include "vt_sh4_fixed.h"
void Sh4Interpreter::ExecuteOpcode(u16 op)
{
    vt_sh4_execute(ctx, op, sh4cycles);
}
'''+wrapper[brace_end(wrapper,b):]
    (output/'vt_sh4_executor.cpp').write_text(wrapper)
    return headers

def media_files():
    info=json.loads((ROOT/'Configuration/media.json').read_text())
    result={}
    for record in info['files']:
        if record['name'] not in (info['bios'],'epr-22927.ic22'):continue
        path=ROOT/'build/assets/vtennis'/record['name']
        if path.stat().st_size!=record['bytes'] or sha(path)!=record['sha256']:
            raise RuntimeError('Unexpected translation media: '+record['name'])
        result[record['name']]=path.read_bytes()
    if len(result)!=2:raise RuntimeError('Missing pinned BIOS or EPR')
    return result,info

def images(files, info):
    result=[('bios',0,files[info['bios']])]
    eprom=files['epr-22927.ic22']
    for name,offset in [('game',0x360),('service',0x3c0)]:
        for index in range(8):
            src,dst,size=struct.unpack_from('<III',eprom,offset+12*index)
            if src==0xffffffff:break
            if src+size>len(eprom) or src%2 or dst%2 or size%2:
                raise RuntimeError('Unverified boot-header segment')
            result.append((name+str(index),dst,eprom[src:src+size]))
    expected=[('bios',0,0x200000),('game0',0xc020000,0x400000),('service0',0xc020000,0x100000)]
    if [(n,p,len(d)) for n,p,d in result]!=expected:
        raise RuntimeError('Pinned boot-header mapping changed')
    copies=json.loads((ROOT/'Configuration/sh4-bios-copies.json').read_text())
    bios=files[copies['bios']]
    if hashlib.sha256(bios).hexdigest()!=copies['biosSHA256']:
        raise RuntimeError('BIOS-copy source identity changed')
    for block in copies['loaderSourceBlocks']:
        if hashlib.sha256(bios[block['offset']:block['offset']+block['bytes']]).hexdigest()!=block['sha256']:
            raise RuntimeError('BIOS-copy loader identity changed')
    for copy in copies['copies']:
        data=bios[copy['source']:copy['source']+copy['bytes']]
        if len(data)!=copy['bytes']:raise RuntimeError('Truncated BIOS copy')
        if copy['transform']=='reverse-halfwords':
            data=b''.join(data[i:i+2] for i in range(len(data)-2,-1,-2))
        elif copy['transform']!='identity':raise RuntimeError('Unknown fixed copy transform')
        result.append((copy['name'],copy['destination'],data))
    bootstrap=next(data for name,base,data in result if name=='bios_bootstrap')
    if struct.unpack_from('<I',bootstrap,0x18)[0]*4!=0x1fff00:
        raise RuntimeError('Original BIOS longword-copy count changed')
    if struct.unpack_from('<III',bios,0x604)!=(0x800,0xc001000,0x3000):
        raise RuntimeError('Original BIOS relocation literals changed')
    if struct.unpack_from('<H',bios,0x33d40)[0]*4!=0x7000 or struct.unpack_from('<I',bios,0x33d44)[0]!=0xac018000 or struct.unpack_from('<I',bios,0x33d4c)[0]!=0xa0060000:
        raise RuntimeError('Original BIOS module loader literals changed')
    if struct.unpack_from('<III',bios,0x1660)!=(0x1474,0x1480,0x1494):
        raise RuntimeError('Original exception-code source/patched-literal pointers changed')
    return result

def write_array(path, name, values):
    with path.open('w') as out:
        out.write('#include "vt_sh4_fixed.h"\nextern const uint32_t '+name+'[] = {\n')
        for index in range(0,len(values),16):
            out.write(','.join(f'0x{v:08x}' for v in values[index:index+16])+',\n')
        out.write('};\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--output',type=Path,default=ROOT/'build/generated/sh4')
    parser.add_argument('--trace',type=Path,action='append',default=[])
    args=parser.parse_args()
    args.trace=[p.resolve() for p in args.trace]
    source=args.source.resolve();requested=args.output.absolute()
    if requested.is_symlink():raise RuntimeError('Generated output must not be a symbolic link')
    output=requested.resolve()
    generated_summary=publish_generation(output,lambda staging:generate(args,source,staging))
    print(json.dumps(dict(generated_summary,output=str(output))))

def publish_generation(output,emit):
    """Generate into a new empty directory; never inventory previous residue."""
    output=Path(output).resolve();build=(ROOT/'build').resolve()
    if not output.is_relative_to(build) or output==build:
        raise RuntimeError('Generated output must be a dedicated directory inside build')
    if output.exists():
        if not output.is_dir():raise RuntimeError('Generated output is not a directory')
        if any(output.iterdir()):
            if not (output/'manifest.json').is_file():
                raise RuntimeError('Refusing to replace an unrecognized generated directory')
            if json.loads((output/'manifest.json').read_text()).get('cpu')!='SH4':
                raise RuntimeError('Existing generated output does not belong to SH4')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+output.name+'-staging-',dir=output.parent) as name:
        staging=Path(name);summary=emit(staging)
        if not (staging/'manifest.json').is_file():raise RuntimeError('Generation did not finish its manifest')
        backup=None
        if output.exists():
            backup=Path(tempfile.mkdtemp(prefix='.'+output.name+'-previous-',dir=output.parent));backup.rmdir();output.rename(backup)
        try:staging.rename(output)
        except BaseException:
            if backup is not None:backup.rename(output)
            raise
        if backup is not None:shutil.rmtree(backup)
        return summary

def generate(args,source,output):
    pins=json.loads((ROOT/'Configuration/sh4-source.json').read_text())
    for rel,digest in pins['files'].items():
        if sha(source/rel)!=digest:raise RuntimeError('Unpinned SH4 source: '+rel)
    table=metadata(source)
    files,info=media_files()
    full_images=images(files,info)
    observed={}
    for trace in args.trace:
        for line in trace.read_text().splitlines():
            event=json.loads(line)
            if event.get('kind')!='instruction':continue
            pc,word=event['pc'],event['opcode']
            if not isinstance(pc,int) or not 0<=pc<=0xffffffff or pc&1 or not 0<=word<=65535:
                raise RuntimeError('Malformed observer entry')
            observed.setdefault(pc,set()).add(word)
    for pc,variants in observed.items():
        physical=pc&0x1fffffff
        for word in variants:
            if pc&0xe0000000 not in (0,0x80000000,0xa0000000) or not any(base<=physical<base+len(data) and struct.unpack_from('<H',data,physical-base)[0]==word for name,base,data in full_images):
                raise RuntimeError(f'Observed instruction lacks a ROM-derived image: PC={pc:08x}, word={word:04x}')
    validated=sum(len(v) for v in observed.values())
    # Traces validate coverage; they never add executable entries. A clean
    # generation from exact user media produces identical fixed C++ sources.
    observed={}
    words=set()
    image_words=[]
    for name,base,data in full_images:
        values=list(struct.unpack('<'+'H'*(len(data)//2),data))
        words.update(values);image_words.append((name,base,values))
    ordered=sorted(words);ids={word:i for i,word in enumerate(ordered)}
    if len(ordered)>65536:raise RuntimeError('SH4 fixed operation index overflow')
    headers=lifecycle(source,output)
    integer,floating=templates(source)
    (output/'vt_integer_templates.h').write_text(integer)
    (output/'vt_floating_templates.h').write_text(floating)
    declarations=['#pragma once','#include "types.h"','#include "hw/sh4/sh4_cycles.h"',
        'using VTFixedOperation = void(*)(Sh4Context*, Sh4Cycles&);',
        'void vt_sh4_execute(Sh4Context*, uint16_t, Sh4Cycles&);',
        'extern "C" [[noreturn]] void vt_fixed_fault(const char*, uint32_t, uint32_t, const char*);']
    functions=[]
    for begin in range(0,len(ordered),512):
        content=['#include "vt_sh4_fixed.h"','#include "hw/sh4/sh4_interpreter.h"',
            '#include "vt_integer_templates.h"','#include "vt_floating_templates.h"','#include "reios/reios.h"']
        for word in ordered[begin:begin+512]:
            row=table[word];name=f'vt_operation_{ids[word]}'
            declarations.append('void '+name+'(Sh4Context*, Sh4Cycles&);')
            functions.append('&'+name)
            call=(f'reios_trap(ctx, 0x{word:04x});' if row['handler']=='reios_trap' else
                f'{"vt_floating" if row["handler"] in floating else "vt_integer"}::{row["handler"]}<0x{word:04x}>(ctx);')
            content.append(f'void {name}(Sh4Context *ctx, Sh4Cycles &cycles) {{')
            if row['floating']:content.append('if (ctx->sr.FD == 1) throw SH4ThrownException(ctx->pc - 2, Sh4Ex_FpuDisabled);')
            content.extend([call,f'cycles.executeFixed<{row["unit"]}, {row["issue"]}, {str(row["memory"]).lower()}>();','}'])
        (output/f'vt_operations_{begin//512:03d}.cpp').write_text('\n'.join(content)+'\n')
    dispatch=['#include "vt_sh4_fixed.h"','#include <algorithm>',
        'extern "C" const char *vt_fixed_sh4_marker() { return "Virtua Tennis fixed offline SH4; PC-selected functions and fetched-word guards"; }',
        'static const VTFixedOperation operations[] = {',','.join(functions),'};',
        'struct VTObserved { uint32_t pc; uint32_t packed; };']
    entries=[(pc,(ids[word]<<16)|word) for pc,variants in sorted(observed.items()) for word in sorted(variants)]
    dispatch.append('static const VTObserved observed[] = {'+','.join(f'{{0x{pc:08x},0x{entry:08x}}}' for pc,entry in entries)+ ('{0xffffffff,0}' if not entries else '')+'};')
    for name,base,values in image_words:
        declarations.append(f'extern const uint32_t vt_image_{name}[];')
        write_array(output/f'vt_image_{name}.cpp','vt_image_'+name,[(ids[v]<<16)|v for v in values])
    dispatch+=['void vt_sh4_execute(Sh4Context *ctx, uint16_t fetched, Sh4Cycles &cycles) {',
        'const uint32_t pc = ctx->pc - 2;',
        '// Only physical, cached P1 and uncached P2 aliases of the exact images.',
        'if ((pc & 0xe0000000) == 0 || (pc & 0xe0000000) == 0x80000000 || (pc & 0xe0000000) == 0xa0000000) {',
        'const uint32_t physical = pc & 0x1fffffff;']
    for name,base,values in image_words:
        dispatch += [f'if (!(physical & 1) && physical >= 0x{base:08x} && physical < 0x{base+2*len(values):08x}) {{',
            f'const uint32_t packed = vt_image_{name}[(physical - 0x{base:08x}) >> 1];',
            'if (uint16_t(packed) == fetched) { operations[packed >> 16](ctx, cycles); return; }','}']
    dispatch+=['}',
        '// Bootstrap copies and observed overwritten code are a guarded fallback.',
        'const auto it = std::lower_bound(std::begin(observed), std::end(observed), pc, [](const VTObserved &entry, uint32_t address) { return entry.pc < address; });',
        'for (auto candidate = it; candidate != std::end(observed) && candidate->pc == pc; ++candidate) {',
        'if (uint16_t(candidate->packed) == fetched) { operations[candidate->packed >> 16](ctx, cycles); return; }','}',
        'vt_fixed_fault("SH4", pc, fetched, "Unknown PC or changed fixed instruction");','}']
    (output/'vt_sh4_dispatch.cpp').write_text('\n'.join(dispatch)+'\n')
    (output/'vt_sh4_fixed.h').write_text('\n'.join(declarations)+'\n')
    from compile_sh4_blocks import emit as emit_run_optimization,emit_layout
    run_optimization=emit_run_optimization(ROOT,source,output,table,full_images,ids)
    from compile_sh4_pure import emit as emit_pure_segments
    run_optimization=emit_pure_segments(ROOT,source,output,table,full_images,run_optimization)
    run_optimization=emit_layout(ROOT,output,full_images,ids,run_optimization)
    result={'cpu':'SH4','runOptimization':run_optimization,'sourcePinsSHA256':sha(ROOT/'Configuration/sh4-source.json'),
        'generatorSHA256':sha(__file__),'upstreamCommit':pins['upstreamCommit'],
        'mediaIdentitySHA256':sha(ROOT/'Configuration/media.json'),'inputSources':pins['files'],
        'biosCopyConfigurationSHA256':sha(ROOT/'Configuration/sh4-bios-copies.json'),
        'sh4ClockHz':200000000,'interpreterCycleMultiplier':1,'strictMode':False,
        'headers':headers,'fixedOperationCount':len(ordered),'observedVariants':len(entries),
        'images':[{'name':n,'base':f'0x{b:08x}','bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()} for n,b,d in full_images],
        'traceSHA256':{str(p.relative_to(ROOT)):sha(p) for p in args.trace},
        'validatedTraceInstructions':validated,'traceRequiredForGeneration':False,
        'generated':{str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name!='manifest.json'},
        'boundary':'PC selects fixed operation; fetched cache-visible word authenticates only. Original cache/MMU and delay-slot fetch ordering retained.',
        'validation':'Generation is not behavioral validation; see separate original/native comparisons.'}
    (output/'manifest.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    return {'fixedOperations':len(ordered),'observedVariants':len(entries),'images':len(full_images)}

if __name__=='__main__':main()
