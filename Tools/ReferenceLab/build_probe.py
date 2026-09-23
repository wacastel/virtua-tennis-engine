#!/usr/bin/env python3
"""Link a distinct original/observer laboratory library with a read-only clock probe."""
from pathlib import Path
import argparse,json,shlex,subprocess
from build_observer import ROOT,SOURCE,REFERENCE,sha,compile_command,link_command

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--observer',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
 out=(a.output or ROOT/('build/observer-clock-probe' if a.observer else 'build/reference-clock-probe')).resolve();out.mkdir(parents=True,exist_ok=True)
 reference=REFERENCE/'flycast_libretro.dylib';before=sha(reference)
 source=Path(__file__).with_name('core_probe.cpp');obj=out/'core_probe.o'
 command,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',source,obj)
 with (out/'compile.log').open('w') as log:subprocess.run(command,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
 replacements={}
 if a.observer:
  obs=ROOT/'build/observer-clock';manifest=json.loads((obs/'manifest.json').read_text())
  if manifest['referenceSHA256']!=before:raise RuntimeError('Observer reference changed')
  for rel in manifest['patches']:
   key='CMakeFiles/flycast_libretro.dir/'+rel+'.o';op=obs/'objects'/(Path(rel).name+'.o')
   if sha(op)!=manifest['objects'][str(op.relative_to(obs))]:raise RuntimeError('Observer object changed')
   replacements[key]=op
 product=out/('flycast_observer_probe.dylib' if a.observer else 'flycast_reference_probe.dylib')
 command=link_command(replacements,product)
 exports=out/'exports.txt';exports.write_text((SOURCE/'shell/libretro/libretro.osx.def').read_text()+'\n_vt_fixed_ticks\n')
 for i,arg in enumerate(command):
  if arg.startswith('-Wl,-exported_symbols_list,'):command[i]='-Wl,-exported_symbols_list,'+str(exports)
 command.append(str(obj))
 with (out/'link.log').open('w') as log:subprocess.run(command,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
 if sha(reference)!=before:raise RuntimeError('Original reference changed')
 report={'purpose':'Original CPU laboratory with one read-only hardware-clock export',
  'referenceSHA256':before,'observer':a.observer,'probeSourceSHA256':sha(source),
  'productSHA256':sha(product),'originalCPUOperationsChanged':False,'shippingMarkerPresent':False,
  'source':str(source.relative_to(ROOT)),'product':str(product.relative_to(ROOT))}
 (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
