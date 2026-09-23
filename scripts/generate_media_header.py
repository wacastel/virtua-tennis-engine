#!/usr/bin/env python3
"""Bind the native host to the exact verified archives it will consume."""
from pathlib import Path
import hashlib,json,zipfile
from build_reference import ROOT,sha

def main():
 assets=ROOT/'build/assets';spec=json.loads((ROOT/'Configuration/media.json').read_text())
 manifest=json.loads((assets/'manifest.json').read_text())
 if manifest['mediaSpecificationSHA256']!=sha(ROOT/'Configuration/media.json'):raise RuntimeError('Media specification changed; import again')
 entries=[]
 for rel,wanted,names in [('vtennis.zip',manifest['archiveSHA256'],{x['name'] for x in spec['files']}),
   ('system/dc/naomi.zip',manifest['biosArchiveSHA256'],{spec['bios']})]:
  path=assets/rel
  if sha(path)!=wanted:raise RuntimeError('Archive identity mismatch: '+rel)
  with zipfile.ZipFile(path) as z:
   if len(z.infolist())!=len(names) or set(z.namelist())!=names:raise RuntimeError('Unexpected or duplicate archive member: '+rel)
   for row in spec['files']:
    if row['name'] not in names:continue
    data=z.read(row['name'])
    if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise RuntimeError('Archive member differs: '+row['name'])
  entries.append((rel,path.stat().st_size,wanted))
 lines=['// SPDX-License-Identifier: GPL-2.0-only','// Generated archive identities; no original game bytes.',
  '#pragma once','#include <stddef.h>','#include <stdint.h>',
  'struct vt_media_entry { const char *path; uint64_t bytes; const char *sha256; };',
  'static constexpr vt_media_entry vt_media[] = {']
 for rel,size,digest in entries:lines.append('    {'+json.dumps(rel)+', '+str(size)+'ULL, '+json.dumps(digest)+'},')
 lines+=['};','static constexpr size_t vt_media_count = sizeof(vt_media) / sizeof(vt_media[0]);','']
 out=ROOT/'Sources/Bridge/media_identity.h';out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(lines))
 print(json.dumps({'header':str(out.relative_to(ROOT)),'consumedArchives':len(entries),'sha256':sha(out)}))
if __name__=='__main__':main()
