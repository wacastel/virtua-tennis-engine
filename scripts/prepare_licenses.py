#!/usr/bin/env python3
"""Preserve pinned upstream license texts and source notices verbatim."""
from pathlib import Path
import hashlib,json,re,shutil
from build_reference import ROOT,SOURCE,REVISION,sha
OUTPUT=ROOT/'Licenses'
SELECTED=[
 'LICENSE','core/deps/asio/asio/LICENSE_1_0.txt','core/deps/asio/asio/COPYING',
 'core/deps/libchdr/LICENSE.txt','core/deps/libchdr/deps/lzma-24.05/LICENSE',
 'core/deps/libchdr/deps/zlib-1.3.1/LICENSE','core/deps/libchdr/deps/zstd-1.5.6/LICENSE',
 'core/deps/libchdr/deps/zstd-1.5.6/COPYING','core/deps/libzip/LICENSE',
 'core/deps/miniupnpc/LICENSE','core/deps/nowide/LICENSE','core/deps/picotcp/COPYING',
 'core/deps/picotcp/LICENSE.GPLv2','core/deps/picotcp/LICENSE.GPLv3',
 'core/deps/tinygettext/LICENSE.md','core/deps/tinygettext/external/tinycmmc/LICENSE.md',
 'core/deps/xxHash/LICENSE']
def main():
 OUTPUT.mkdir(exist_ok=True)
 inventory=[]
 for rel in SELECTED:
  source=SOURCE/rel
  if not source.is_file():raise RuntimeError('Missing upstream license '+rel)
  target=OUTPUT/'Upstream'/(rel+'.txt')
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
  inventory.append({'upstreamPath':rel,'preservedPath':str(target.relative_to(ROOT)),'sha256':sha(source),'bytes':source.stat().st_size})
 shutil.copy2(SOURCE/'LICENSE',ROOT/'COPYING')
 # Include leading comment notices from implementation/header files used by the
 # core, including embedded third-party code whose notices live only in source.
 notices=[]
 for base in [SOURCE/'core',SOURCE/'shell/libretro']:
  for source in sorted(base.rglob('*')):
   if not source.is_file() or source.suffix not in {'.c','.cpp','.h','.hpp','.mm','.cc'}:continue
   rel=str(source.relative_to(SOURCE))
   if '/.git/' in rel:continue
   text=source.read_text(errors='replace')
   marker=re.match(r'\s*((?:/\*.*?\*/|//[^\n]*(?:\n|$)|\s)+)',text,re.S)
   leading=marker.group(1).strip() if marker else ''
   if not re.search(r'copyright|license|licence|permission|SPDX',leading,re.I):continue
   notices.append((rel,leading,sha(source)))
 parts=['Pinned Flycast source notices\nUpstream commit: '+REVISION+'\n\nThese are verbatim leading source notices. Individual terms remain authoritative.\n']
 for rel,notice,digest in notices:parts.append('\n'+rel+'\nSource SHA-256: '+digest+'\n\n'+notice+'\n')
 (OUTPUT/'Source-Notices.txt').write_text(''.join(parts))
 report={'upstreamCommit':REVISION,'licenseFiles':inventory,
  'sourceNotices':len(notices),'sourceNoticeCollectionSHA256':sha(OUTPUT/'Source-Notices.txt'),
  'scope':'Preserved license files and leading source notices for the core and libretro subtree. The notice collection includes unused bundled source as well as linked source; it is not a declaration that all those components ship.',
  'noticeSources':[{'path':r,'sourceSHA256':h} for r,n,h in notices]}
 (ROOT/'Documentation/third-party-notices.json').write_text(json.dumps(report,indent=2)+'\n')
 (OUTPUT/'README.md').write_text('# Source licenses\n\nFlycast is distributed under GPL version 2; its component notices remain applicable. Exact upstream license texts and leading source notices are preserved here, with hashes in `Documentation/third-party-notices.json`. Local source additions use GPL-2.0 unless explicitly marked otherwise. Game media, Sega trademarks and the original app artwork are separate from these engine source licenses. User-supplied media and generated game programs are excluded from Git.\n')
 print(json.dumps({'licenseFiles':len(inventory),'sourceNotices':len(notices)}))
if __name__=='__main__':main()
