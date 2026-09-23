#!/usr/bin/env python3
"""Verify user-supplied media and create a deterministic local game archive."""
from pathlib import Path
import argparse,hashlib,json,shutil,zipfile,zlib
ROOT=Path(__file__).resolve().parents[1]
def identify(data):
    return {'bytes':len(data),'crc32':f'{zlib.crc32(data)&0xffffffff:08x}',
        'sha1':hashlib.sha1(data).hexdigest(),'sha256':hashlib.sha256(data).hexdigest()}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--media-root',type=Path,default=ROOT/'Virtua Tennis ROMs')
    p.add_argument('--output',type=Path,default=ROOT/'build/assets')
    args=p.parse_args();src=args.media_root.resolve();out=args.output.resolve()
    if not src.is_dir():p.error('Supplied media folder does not exist')
    spec=json.loads((ROOT/'Configuration/media.json').read_text())
    verified={}
    # Search only this explicitly supplied media tree, never the user's home.
    paths={}
    for f in src.rglob('*'):
        if f.is_file() and not f.is_symlink():paths.setdefault(f.name,[]).append(f)
    for row in spec['files']:
        expected={k:row[k] for k in ['bytes','crc32','sha1','sha256']}
        for f in sorted(paths.get(row['name'],[])):
            if f.stat().st_size!=row['bytes']:continue
            data=f.read_bytes()
            if identify(data)==expected:verified[row['name']]=data;break
        if row['name'] not in verified:
            for archive in sorted(src.rglob('*.zip')):
                with zipfile.ZipFile(archive) as z:
                    for member in z.infolist():
                        if Path(member.filename).name==row['name'] and member.file_size==row['bytes']:
                            data=z.read(member)
                            if identify(data)==expected:verified[row['name']]=data;break
                if row['name'] in verified:break
        if row['name'] not in verified:raise RuntimeError('Missing or mismatched media: '+row['name'])
    out.mkdir(parents=True,exist_ok=True);(out/'vtennis').mkdir(exist_ok=True)
    for name,data in verified.items():(out/'vtennis'/name).write_bytes(data)
    temporary=out/'vtennis.zip.partial'
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted(verified.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.external_attr=0o100644<<16
            z.writestr(info,data)
    temporary.replace(out/'vtennis.zip')
    bios_dir=out/'system/dc';bios_dir.mkdir(parents=True,exist_ok=True)
    bios_temp=bios_dir/'naomi.zip.partial'
    with zipfile.ZipFile(bios_temp,'w',compression=zipfile.ZIP_STORED) as z:
        info=zipfile.ZipInfo(spec['bios'],date_time=(1980,1,1,0,0,0));info.external_attr=0o100644<<16
        z.writestr(info,verified[spec['bios']])
    bios_temp.replace(bios_dir/'naomi.zip')
    with zipfile.ZipFile(out/'vtennis.zip') as z:
        assert set(z.namelist())==set(verified)
        for name,data in verified.items():assert z.read(name)==data
    manifest={'schema':1,'verifiedFiles':spec['files'],'archive':'vtennis.zip',
        'archiveSHA256':identify((out/'vtennis.zip').read_bytes())['sha256'],
        'biosArchive':'system/dc/naomi.zip',
        'biosArchiveSHA256':identify((bios_dir/'naomi.zip').read_bytes())['sha256'],
        'mediaSpecificationSHA256':hashlib.sha256((ROOT/'Configuration/media.json').read_bytes()).hexdigest(),
        'suppliedSourcesModified':False,'downloadedMedia':False}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'verifiedFiles':len(verified),'archive':str(out/'vtennis.zip'),'sha256':manifest['archiveSHA256']}))
if __name__=='__main__':main()
