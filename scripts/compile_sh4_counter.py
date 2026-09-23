"""ROM-derived proof and emission for the single reviewed counter-loop contraction."""
from pathlib import Path
import hashlib,struct
from validate_sh4_blocks import require

SPANS=((0x0c0c183a,4),(0x0c0bf320,26),(0x0c0be080,4),(0x0c0bf33a,8),(0x0c0c183e,16),(0x0c0c184e,8))
DELAY_BRANCHES={0x0c0c183a,0x0c0bf336,0x0c0be080,0x0c0bf33e}
def digest(data):return hashlib.sha256(data).hexdigest()

def validate(config,images,metadata,bodies):
    require(config['entryPC']=='0x0c0c183a' and config['maximumIterations']==14 and config['instructionsPerIteration']==33 and config['memoryOperationsPerIteration']==14 and config['cyclesPerIteration']==30,'Counter-loop bounds changed')
    require(config['completeContinuingIterationsOnly'] is True and config['cyclesAfterAtLeast']==1 and config['counterAfterAtLeast']==1 and config['busEventCountsPreserved'] is False and config['developmentTelemetry'] is False,'Counter-loop observation boundary changed')
    image=next((b,d) for n,b,d in images if n=='game0');base,data=image
    require(tuple((int(r['pc'],16),r['bytes']) for r in config['codeSpans'])==SPANS,'Counter-loop instruction spans changed')
    words={};spans=[];hashes={}
    for span,(pc,size) in zip(config['codeSpans'],SPANS):
        offset=pc-base;require(span['sourceOffset']==offset,'Counter-loop source offset changed');raw=data[offset:offset+size]
        require(len(raw)==size and digest(raw)==span['sha256'],'Counter-loop original code hash differs')
        opwords=list(struct.unpack('<'+'H'*(size//2),raw));spans.append(dict(span,words=opwords))
        for i,word in enumerate(opwords):
            address=pc+2*i;words[address]=word;row=metadata[word];handler=row['handler']
            require(handler in config['originalBodySHA256'] and digest(bodies[handler].encode())==config['originalBodySHA256'][handler],'Counter-loop original operation body changed')
            hashes[handler]=digest(bodies[handler].encode())
    require(set(hashes)==set(config['originalBodySHA256']) and len(words)==33,'Counter-loop original operation inventory changed')
    literal=config['literal'];require(literal['pc']=='0x0c0bf360' and literal['bytes']==4,'Counter-loop literal location changed')
    raw=data[0x0c0bf360-base:0x0c0bf364-base];require(digest(raw)==literal['sha256'],'Counter-loop literal identity changed');value=struct.unpack('<I',raw)[0]
    # Original slots charge before their owning branch. The continuing path
    # executes all six spans in order, with no other control-flow case admitted.
    order=[]
    for pc,size in SPANS:
        address=pc
        while address<pc+size:
            if address in DELAY_BRANCHES:
                order.extend((address+2,address));address+=4
            else:order.append(address);address+=2
    require(len(order)==33 and set(order)==set(words),'Counter-loop charge order changed')
    last='BR';cost=0;memory=0
    for address in order:
        row=metadata[words[address]];unit=row['unit'];memory+=int(row['memory'])
        require(not row['floating'],'Counter-loop unexpectedly uses floating-point state')
        if last=='CO' or unit=='CO' or (last==unit and last!='MT'):last=unit;cost+=row['issue']
        else:last='CO'
    require(cost==30 and last=='BR' and memory==14,'Counter-loop cycle or memory metadata changed')
    return {'spans':spans,'literalValue':value,'chargeOrderPCs':[f'0x{x:08x}' for x in order],'originalBodySHA256':hashes,'cycles':cost,'memoryOperations':memory}

def add_cycle_methods(root,output):
    template=Path(root)/'Sources/Translated/sh4/templates/counter_cycle_methods.h.in';methods=template.read_text()
    path=Path(output)/'overlay/core/hw/sh4/sh4_cycles.h';text=path.read_text();marker='private:\n\t// Returns the number of external cycles'
    require(text.count(marker)==1 and 'vtCounterLoopReady' not in text,'Original fixed-cycle class shape changed')
    derived=text.replace(marker,methods+marker);require(derived.replace(methods,'')==text,'Cycle-method inverse transformation failed');path.write_text(derived)
    return {'methodTemplateSHA256':digest(methods.encode()),'classLayoutUnchanged':True,'originalClassSHA256':digest(text.encode()),'inverseSourceTransformationVerified':True}

def emit(root,output,validated,cycle_info):
    root,output=Path(root),Path(output);templates=root/'Sources/Translated/sh4/templates';text=(templates/'counter_loop.cpp.in').read_text();guards=[];grouping=[]
    require(text.count('@@CODE_GUARDS@@')==1 and text.count('@@TABLE_LITERAL@@')==1,'Counter-loop template markers changed')
    for index,span in enumerate(validated['spans']):
        guards.append(f'    if (!ramSpan(alias|{span["pc"]}u,{span["bytes"]},code[{index}])) return false;')
        raw=struct.pack('<'+'H'*len(span['words']),*span['words']);require(len(raw)==span['bytes'],'Counter-loop code span length changed')
        offset=0;reconstructed=bytearray();groups=[]
        while offset<len(raw):
            width=next(n for n in (8,4,2) if n<=len(raw)-offset)
            value=int.from_bytes(raw[offset:offset+width],'little');address=f'code[{index}].address+{offset}u'
            expr=f'load64({address})' if width==8 else f'load32({{{address},4}})' if width==4 else f'load16({address})'
            suffix='ull' if width==8 else 'u'
            guards.append(f'    if ({expr}!=0x{value:0{2*width}x}{suffix}) return false;')
            reconstructed.extend(value.to_bytes(width,'little'));groups.append({'offset':offset,'bytes':width});offset+=width
        require(bytes(reconstructed)==raw and offset==span['bytes'],'Grouped counter guards changed original bytes or overread')
        grouping.append({'span':index,'bytes':len(raw),'originalWordCount':len(span['words']),'groups':groups,'reconstructedOriginalBytesSHA256':digest(raw),'noOverread':True})
    text=text.replace('@@CODE_GUARDS@@','\n'.join(guards)).replace('@@TABLE_LITERAL@@',f'0x{validated["literalValue"]:08x}')
    require('VT_COUNTER_LOOP_REPORT' not in text and 'destructor' not in text and 'attempts' not in text,'Laboratory telemetry leaked into canonical helper')
    (output/'counter_loop.cpp').write_text(text);(output/'counter_loop.h').write_bytes((templates/'counter_loop.h').read_bytes())
    return {'entryPC':'0x0c0c183a','cyclesAfterAtLeast':1,'counterAfterAtLeast':1,'originalFallbackUnchanged':True,'helperSource':'counter_loop.cpp','helperHeader':'counter_loop.h','completeContinuingIterationsOnly':True,'maximumIterations':14,'instructionsPerIteration':33,'cyclesPerIteration':30,'memoryOperationsPerIteration':14,'chargeOrderPCs':validated['chargeOrderPCs'],'originalBodySHA256':validated['originalBodySHA256'],'cycleMethods':cycle_info,'busEventCountsPreserved':False,'laboratoryTelemetry':False,'groupedCodeAuthentication':{'targetEndian':'little-endian arm64','spans':grouping,'allRemainingGuardsUnchanged':True,'sourceWordsFromAuthenticatedMediaOnly':True}}
