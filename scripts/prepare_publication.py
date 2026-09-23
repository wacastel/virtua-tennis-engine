#!/usr/bin/env python3
"""Stage a bounded, reviewed source companion locally; never invoke Git or publish."""
from __future__ import annotations
import argparse,ast,base64,hashlib,json,re,shutil,stat,struct,tempfile
from pathlib import Path
from build_reference import ROOT,REVISION,sha,save

REVIEW=ROOT/'build/publication-review'
PAYLOAD=REVIEW/'engine-source'
ALLOWLIST=ROOT/'Configuration/publication-files.json'
TEXT_SUFFIXES={'.py','.sh','.command','.cpp','.cc','.c','.h','.hpp','.mm','.swift','.md','.json','.lua','.txt','.plist','.in'}
SOURCE_TREES={'Sources','scripts','Tools','Configuration','Documentation','Licenses'}
ROOT_SOURCES={'build.sh','Play.command'}
IGNORE='/build/\n/Assets/\n/Virtua Tennis ROMs/\n/Publication/\n/Saves/\n/Captures/\n/snap/\n/cfg/\n__pycache__/\n*.pyc\n.DS_Store\n*.zip\n*.7z\n*.tar.gz\n*.bin\n*.rom\n*.chd\n*.nv\n*.nvram\n*.o\n*.a\n*.dylib\n*.app/\n*.log\n'
PRIVATE_PATH=re.compile(rb'/(?:'+b'Users|home'+rb')/[^/\s"\x27]+/')
CREDENTIALS=[re.compile(x) for x in (
    rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{30,}',
    rb'AKIA[0-9A-Z]{16}',rb'xox[baprs]-[A-Za-z0-9-]{20,}',
    rb'sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{20,}',
    rb'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----')]

def digest(data):return hashlib.sha256(data).hexdigest()

def inventory():
    names=json.loads(ALLOWLIST.read_text())
    if not isinstance(names,list) or not all(isinstance(x,str) for x in names):raise RuntimeError('Allowlist must be an explicit array of paths')
    if len(names)!=len(set(names)):raise RuntimeError('Duplicate source allowlist path')
    paths=[]
    for name in names:
        rel=Path(name);path=ROOT/rel
        if rel.is_absolute() or '..' in rel.parts or str(rel)!=name or not path.is_file():raise RuntimeError('Missing or unsafe source path: '+name)
        if not path.resolve().is_relative_to(ROOT.resolve()) or any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(ROOT)):raise RuntimeError('Symlinked source path: '+name)
        if rel.parts[0] not in SOURCE_TREES and name not in ROOT_SOURCES:raise RuntimeError('Unreviewed source tree: '+name)
        if path.suffix not in TEXT_SUFFIXES or '__pycache__' in rel.parts:raise RuntimeError('Unreviewed source extension: '+name)
        paths.append(rel)
    return sorted(paths)

def media_needles():
    spec=json.loads((ROOT/'Configuration/media.json').read_text()); rows=spec['files']
    if spec['set']!='vtennis' or spec['bios']!='epr-21577h.ic27' or len(rows)!=13:raise RuntimeError('Unreviewed game/media inventory')
    known={}
    for row in rows:
        p=ROOT/'build/assets/vtennis'/row['name']
        if p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise RuntimeError('Changed local media: '+row['name'])
        known[row['name']]=p.read_bytes()
    # Reconstruct executable images from pinned original load recipes, never
    # from generated C++ or a checked-in copy of a program.
    from compile_sh4 import images
    transformed={'sh4-'+name:data for name,address,data in images(known,spec)}
    arm=json.loads((ROOT/'Configuration/arm7-programs.json').read_text())
    for index,row in enumerate(arm['programWindows']):
        raw=known[row['name']]
        if digest(raw)!=row['fileSHA256']:raise RuntimeError('Changed ARM source media')
        data=raw[row['offset']:row['offset']+row['bytes']]
        if len(data)!=row['bytes'] or digest(data)!=row['windowSHA256']:raise RuntimeError('Changed ARM program window')
        transformed['arm7-window-'+str(index)]=data
    dsp=json.loads((ROOT/'Configuration/aicadsp-program.json').read_text()); raw=known[dsp['name']]
    if digest(raw)!=dsp['fileSHA256']:raise RuntimeError('Changed DSP source media')
    full=raw[dsp['offset']:dsp['offset']+dsp['bytes']]
    if len(full)!=2048 or digest(full)!=dsp['programSHA256']:raise RuntimeError('Changed DSP program window')
    words=struct.unpack('<512I',full);programs=set()
    for count in range(513):
        programs.add(struct.pack('<512I',*(words[:count]+(0,)*(512-count))))
        programs.add(struct.pack('<512I',*((0,)*count+words[count:])))
    for i,data in enumerate(sorted(programs)):transformed['aicadsp-program-'+str(i)]=data
    needles={**known,**transformed}
    return needles,{'verifiedMediaFiles':len(known),'transformedImages':len(transformed),
        'transformedImageSHA256':{name:digest(value) for name,value in transformed.items()},
        'mediaSpecificationSHA256':sha(ROOT/'Configuration/media.json'),
        'recipeSHA256':{str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'scripts/compile_sh4.py',ROOT/'Configuration/sh4-bios-copies.json',ROOT/'Configuration/arm7-programs.json',ROOT/'Configuration/aicadsp-program.json']}}

def content_issues(name,data,needles,media_names):
    issues=[]
    if Path(name).name in media_names:issues.append('original media filename')
    # Ignore ASCII whitespace in encoded data, including wrapped base64/hex.
    compact=None;lower=None
    for label,value in needles.items():
        if data==value or (len(data)>=len(value) and value in data):issues.append('complete raw media/program: '+label)
        if len(data)>=len(value)*2:
            if compact is None:compact=re.sub(rb'\s+',b'',data)
            if lower is None:lower=compact.lower()
            if value.hex().encode() in lower:issues.append('complete hex media/program: '+label)
        if len(data)>=((len(value)+2)//3)*4:
            if compact is None:compact=re.sub(rb'\s+',b'',data)
            if base64.b64encode(value) in compact:issues.append('complete base64 media/program: '+label)
    if PRIVATE_PATH.search(data):issues.append('local user directory')
    if any(p.search(data) for p in CREDENTIALS):issues.append('credential-shaped content')
    try:data.decode('utf-8')
    except UnicodeDecodeError:issues.append('invalid UTF-8 source text')
    if Path(name).suffix=='.py':
        try:ast.parse(data,filename=name)
        except (SyntaxError,UnicodeDecodeError):issues.append('invalid Python source')
    return issues

def scan(payload=PAYLOAD):
    paths=inventory();allowed={str(p) for p in paths}|{'README.md','.gitignore','COPYING'}
    needles,identity=media_needles(); names={row['name'] for row in json.loads((ROOT/'Configuration/media.json').read_text())['files']}
    issues=[];count=0;size=0
    if payload.is_symlink():raise RuntimeError('Payload must not be a symlink')
    for p in sorted(payload.rglob('*')):
        rel=str(p.relative_to(payload))
        if p.is_symlink():issues.append((rel,'symlink'));continue
        if not p.is_file():continue
        data=p.read_bytes();count+=1;size+=len(data)
        if rel not in allowed:issues.append((rel,'file outside explicit source allowlist'))
        if p.suffix not in TEXT_SUFFIXES and p.name not in {'.gitignore','COPYING'}:issues.append((rel,'non-source extension'))
        issues.extend((rel,reason) for reason in content_issues(rel,data,needles,names))
    actual={str(p.relative_to(payload)) for p in payload.rglob('*') if p.is_file()}
    if actual!=allowed:issues.append(('<inventory>','missing or unexpected payload files'))
    if issues:raise RuntimeError(json.dumps(issues,indent=2))
    return {'passed':True,'files':count,'bytes':size,**identity,
        'rawOrEncodedCompleteMediaMatches':0,'privatePathMatches':0,'credentialFormatMatches':0,
        'scope':'Every explicitly listed staged text file checked for all selected complete media files and reconstructed SH4/ARM7/AICA DSP program images, in raw, contiguous or whitespace-wrapped hex/base64 form. Symlinks, unlisted files, local-user directory strings and common credential formats are rejected. This bounded scan does not classify arbitrary excerpts, all encodings, all secrets or copyright ownership.'}

def file_row(path,source=None):
    if source and (sha(path)!=sha(source) or stat.S_IMODE(path.stat().st_mode)!=stat.S_IMODE(source.stat().st_mode)):
        raise RuntimeError('Source changed while staging: '+str(source.relative_to(ROOT)))
    return {'path':str(path.relative_to(PAYLOAD)),'bytes':path.stat().st_size,'sha256':sha(path),
        'mode':stat.S_IMODE(path.stat().st_mode),'sourcePath':str(source.relative_to(ROOT)) if source else None,
        'sourceSHA256':sha(source) if source else None,'sourceMode':stat.S_IMODE(source.stat().st_mode) if source else None}

def prepare():
    paths=inventory()
    if REVIEW.is_symlink() or not REVIEW.resolve().is_relative_to((ROOT/'build').resolve()):raise RuntimeError('Unsafe review directory')
    REVIEW.mkdir(parents=True,exist_ok=True)
    if PAYLOAD.exists():
        if PAYLOAD.is_symlink():raise RuntimeError('Unsafe existing payload')
        shutil.rmtree(PAYLOAD)
    sources={}
    for rel in paths:
        target=PAYLOAD/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target);sources[str(rel)]=ROOT/rel
    readme=ROOT/'Documentation/engine-source-readme.md'
    shutil.copy2(readme,PAYLOAD/'README.md');sources['README.md']=readme
    (PAYLOAD/'.gitignore').write_text(IGNORE)
    license_source=ROOT/'COPYING'
    if sha(license_source)!=sha(ROOT/'Licenses/Upstream/LICENSE.txt'):raise RuntimeError('Top-level COPYING differs from preserved upstream license')
    shutil.copy2(license_source,PAYLOAD/'COPYING');sources['COPYING']=license_source
    content=scan()
    files=[file_row(p,sources.get(str(p.relative_to(PAYLOAD)))) for p in sorted(PAYLOAD.rglob('*')) if p.is_file()]
    manifest={'status':'Local source-only review payload; no publication or independent companion rebuild performed',
        'publicationActionsPerformed':False,'upstreamCommit':REVISION,'scriptSHA256':sha(Path(__file__)),
        'allowlistSHA256':sha(ALLOWLIST),'contentScan':content,'files':files}
    save(REVIEW/'engine-companion-manifest.json',manifest)
    print(json.dumps({'prepared':True,'files':content['files'],'bytes':content['bytes'],'payload':str(PAYLOAD.relative_to(ROOT)),'publicationActionsPerformed':False}))

def verify():
    manifest=json.loads((REVIEW/'engine-companion-manifest.json').read_text())
    if sha(Path(__file__))!=manifest['scriptSHA256'] or sha(ALLOWLIST)!=manifest['allowlistSHA256']:raise RuntimeError('Scanner or allowlist changed; restage for review')
    actual={str(p.relative_to(PAYLOAD)) for p in PAYLOAD.rglob('*') if p.is_file()}
    if actual!={r['path'] for r in manifest['files']}:raise RuntimeError('Payload inventory changed')
    for row in manifest['files']:
        p=PAYLOAD/row['path']
        if sha(p)!=row['sha256'] or stat.S_IMODE(p.stat().st_mode)!=row['mode']:raise RuntimeError('Payload content/mode changed: '+row['path'])
        if row['sourcePath']:
            source=ROOT/row['sourcePath']
            if sha(source)!=row['sourceSHA256'] or stat.S_IMODE(source.stat().st_mode)!=row['sourceMode']:raise RuntimeError('Source content/mode changed: '+row['sourcePath'])
            if row['mode']!=row['sourceMode']:raise RuntimeError('Source executable mode not preserved')
    if scan()!=manifest['contentScan']:raise RuntimeError('Content scan/recipe changed')
    save(REVIEW/'source-boundary-acceptance.json',{'passed':True,'manifestSHA256':sha(REVIEW/'engine-companion-manifest.json'),
        'scriptSHA256':sha(Path(__file__)),'allowlistSHA256':sha(ALLOWLIST),
        'scope':'Exact source inventory and bounded content scan only; no independent rebuild, gameplay or publication claim.'})
    print('Exact source inventory and bounded media/credential scan passed. Nothing published.')

def self_test():
    global ROOT,ALLOWLIST
    # Synthetic bytes are generated at runtime, not copied from game media.
    sample=bytes(range(256))*4;needles={'synthetic':sample};results=[]
    cases=[('raw',b'prefix'+sample+b'suffix','complete raw'),
        ('hex',sample.hex().encode(),'complete hex'),
        ('uppercase wrapped hex',b'\n'.join(sample.hex().upper().encode()[i:i+64] for i in range(0,2048,64)),'complete hex'),
        ('base64',base64.b64encode(sample),'complete base64'),
        ('wrapped base64',b'\n'.join(base64.b64encode(sample)[i:i+76] for i in range(0,len(base64.b64encode(sample)),76)),'complete base64'),
        ('private directory',b'/'+b'Users'+b'/example/Documents/test','local user'),
        ('credential',b'github_'+b'pat_'+b'A'*40,'credential-shaped'),
        ('nontext',b'\xff\xfe','invalid UTF-8')]
    for name,data,expected in cases:
        found=content_issues('Documentation/check.txt',data,needles,set())
        if not any(expected in item for item in found):raise RuntimeError('Negative scanner control failed: '+name)
        results.append({'case':name,'rejected':True})
    if content_issues('README.md',b'Ordinary source text with an identity hash: '+digest(sample).encode(),needles,set()):raise RuntimeError('Clean text/hash control failed')
    inventory_results=[]; original_root,original_allowlist=ROOT,ALLOWLIST
    with tempfile.TemporaryDirectory(prefix='vt-source-boundary-') as directory:
        try:
            ROOT=Path(directory);ALLOWLIST=ROOT/'allowlist.json'
            (ROOT/'Sources').mkdir();(ROOT/'Sources/ok.py').write_text('value = 1\n')
            (ROOT/'Sources/link.py').symlink_to(ROOT/'Sources/ok.py')
            ALLOWLIST.write_text(json.dumps(['Sources/ok.py']))
            if inventory()!=[Path('Sources/ok.py')]:raise RuntimeError('Clean inventory control failed')
            for label,paths in [('duplicate',['Sources/ok.py','Sources/ok.py']),('symlink',['Sources/link.py']),
                ('parent traversal',['Sources/../Sources/ok.py']),('absolute',[str(ROOT/'Sources/ok.py')]),
                ('missing',['Sources/missing.py']),('excluded tree',['Assets/image.py'])]:
                if label=='excluded tree':(ROOT/'Assets').mkdir();(ROOT/'Assets/image.py').write_text('value = 1\n')
                ALLOWLIST.write_text(json.dumps(paths))
                try:inventory()
                except RuntimeError:inventory_results.append({'case':label,'rejected':True})
                else:raise RuntimeError('Negative inventory control failed: '+label)
        finally:ROOT,ALLOWLIST=original_root,original_allowlist
    result={'passed':True,'negativeCases':results,'inventoryNegativeCases':inventory_results,
        'cleanTextAndHashAccepted':True,'cleanInventoryAccepted':True,'scannerSHA256':sha(Path(__file__)),
        'scope':'Synthetic content-scanner controls only; no staged payload or publication claim.'}
    save(REVIEW/'scanner-self-test.json',result)
    print(json.dumps({'passed':True,'negativeCases':len(results)+len(inventory_results),'cleanControls':2}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--verify',action='store_true');mode.add_argument('--self-test',action='store_true');args=parser.parse_args()
    self_test() if args.self_test else verify() if args.verify else prepare()
