#!/usr/bin/env python3
"""Rebuild all fixed CPU programs from verified local media and pinned sources."""
from pathlib import Path
import argparse,hashlib,json,platform,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--jobs',type=int,default=6)
 a=p.parse_args()
 if a.jobs<1:p.error('--jobs must be positive')
 if platform.system()!='Darwin' or platform.machine()!='arm64':p.error('Apple Silicon macOS is required')
 if sys.version_info<(3,11):p.error('Python 3.11 or later is required')
 started=time.time();executed=[]
 def run(script,*arguments):
  command=[sys.executable,str(ROOT/script),*map(str,arguments)]
  print('Running '+script,flush=True)
  subprocess.run(command,cwd=ROOT,check=True)
  executed.append({'script':script,'sha256':sha(ROOT/script),'arguments':list(map(str,arguments))})
 # This also authenticates every member of both locally imported archives.
 run('scripts/generate_media_header.py')
 run('scripts/verify_native_provenance.py')
 run('scripts/build_reference.py','--jobs',a.jobs)
 run('scripts/build_reference_clock.py','--jobs',a.jobs)
 run('scripts/prepare_licenses.py')
 # Generation consumes only pinned media and loader-derived immutable images.
 # Captured execution traces are optional independent validation, never needed
 # to author an instruction or to supply an interpreter fallback at runtime.
 for cpu in ('sh4','arm7','aicadsp'):
  run('scripts/compile_'+cpu+'.py')
  run('scripts/build_'+cpu+'.py','--jobs',a.jobs)
 verification=ROOT/'build/verification'/('rebuild-'+str(time.time_ns()))
 run('scripts/verify_sh4_metadata.py','--output',verification/'sh4-metadata')
 run('scripts/verify_sh4_operations.py','--output',verification/'sh4-operations')
 # These tests link the actual canonical objects against the isolated original
 # CPU oracle. No historical performance candidate or trace is a build input.
 for name in ('blocks','fetch','chain','read32','counter','pure'):
  run('Tools/ReferenceLab/verify_sh4_'+name+'.py','--canonical','--output',verification/('sh4-'+name))
 run('scripts/verify_arm7.py','--output',verification/'arm7')
 run('scripts/verify_arm7_dispatch.py','--output',verification/'arm7-dispatch')
 run('scripts/verify_aicadsp.py','--output',verification/'aicadsp')
 run('scripts/build_engine.py')
 run('scripts/verify_sh4_targets.py','--library',ROOT/'build/native/libvirtua_tennis.dylib','--output',verification/'sh4-targets.json')
 reports=[ROOT/'Documentation/sh4-metadata-acceptance.json',ROOT/'Documentation/sh4-operation-acceptance.json']
 reports.extend(verification/name/'acceptance.json' for name in ('sh4-blocks','sh4-fetch','sh4-chain','sh4-read32','sh4-counter','sh4-pure','arm7','arm7-dispatch','aicadsp'))
 reports.append(verification/'sh4-targets.json')
 report={'passed':True,'purpose':'Reproducible ROM-rooted fixed engine build and CPU fixture qualification',
  'scriptSHA256':sha(__file__),'steps':executed,'engineManifestSHA256':sha(ROOT/'build/native/manifest.json'),
  'verificationReports':{str(path.relative_to(ROOT)):sha(path) for path in reports},
  'elapsedSeconds':round(time.time()-started,3),
  'scope':'CPU fixtures and build provenance. Packaging, replay parity and real-time play are separately qualified.'}
 (ROOT/'build/native/preparation.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'passed':True,'engine':'build/native/libvirtua_tennis.dylib'}))
if __name__=='__main__':main()
