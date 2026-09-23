#!/usr/bin/env python3
"""Validate BIOS copy derivations against original-execution RAM snapshots."""
import argparse,hashlib,json
from pathlib import Path
from compile_sh4 import ROOT,sha,media_files,images

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace',type=Path,default=ROOT/'build/verification/sh4-play-rtc.jsonl')
    a=p.parse_args();trace=a.trace.resolve()
    records=[json.loads(line) for line in trace.read_text().splitlines()]
    executions={r['pc'] for r in records if r['kind']=='instruction'}
    config=json.loads((ROOT/'Configuration/sh4-bios-copies.json').read_text())
    files,identity=media_files();derived={n:(b,d) for n,b,d in images(files,identity)}
    # Samples are independently observed original memory: reversed bootstrap,
    # a full main-copy code page, and the first full relocated bootstrap page.
    samples=[('bios_bootstrap',0xc0000e0,32),('bios_ram',0xc04a000,4096),('bios_relocated',0xc001000,4096),
        ('bios_module',0xc019000,4096),('bios_exception_return',0xc000620,12),
        ('bios_vector_100',0xc000100,20),('bios_vector_400',0xc000400,20),('bios_vector_600',0xc000600,20)]
    checks=[]
    for name,address,size in samples:
        base,data=derived[name];expected=data[address-base:address-base+size]
        offset=address&0x1ffffff
        matches=[]
        for record in records:
            if record['kind']!='page' or not record['offset']<=offset or offset+size>record['offset']+record['bytes']:continue
            path=Path(str(trace)+'.data')/record['file'];raw=path.read_bytes();start=offset-record['offset']
            if raw[start:start+size]==expected:
                matches.append({'page':record['number'],'snapshotSHA256':sha(path)})
        if not matches:raise RuntimeError('No original exact-byte witness for '+name)
        copy=next(c for c in config['copies'] if c['name']==name)
        absent=[pc for pc in copy['loaderPCs'] if int(pc,16) not in executions]
        if absent:raise RuntimeError('Original copy loader was not executed: '+str(absent))
        checks.append({'image':name,'sampleAddress':f'0x{address:08x}','sampleBytes':size,
            'sampleSHA256':hashlib.sha256(expected).hexdigest(),'originalWitnesses':matches,'loaderPCsObserved':copy['loaderPCs']})
    report={'passed':True,'traceSHA256':sha(trace),'copyConfigurationSHA256':sha(ROOT/'Configuration/sh4-bios-copies.json'),
        'generatorSHA256':sha(ROOT/'scripts/compile_sh4.py'),'verifierSHA256':sha(__file__),'checks':checks,
        'scope':'Original executed loader bounds plus exact sampled original RAM bytes; not a claim that every translated byte is executable.'}
    (ROOT/'Documentation/sh4-image-acceptance.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'passed':True,'imageWitnesses':len(checks)}))

if __name__=='__main__':main()
