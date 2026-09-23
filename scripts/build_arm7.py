#!/usr/bin/env python3
"""Compile fixed sound processors with the exact original Flycast target flags."""
from __future__ import annotations
import argparse,concurrent.futures,hashlib,json,shlex,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import SOURCE,REFERENCE
NINJA=ROOT/'build/tooling/bin/ninja'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def rooted(p):return Path(p) if Path(p).is_absolute() else ROOT/p
def display(p):return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
def dependency_pins(original_sources):
 manifest=json.loads((REFERENCE/'build-manifest.json').read_text())
 inventory=REFERENCE/'upstream-files.json'
 if sha(inventory)!=manifest['upstreamFileInventorySHA256']:raise RuntimeError('Changed upstream source inventory')
 trees=json.loads(inventory.read_text());expected={}
 for base,tree in trees.items():
  for name,digest in tree['files'].items():expected[(SOURCE/base/name).resolve()]=digest
 for patch in manifest.get('sourcePatches',[]):expected[(SOURCE/patch['path']).resolve()]=patch['derivedSHA256']
 pins={inventory:sha(inventory)}
 for relative in original_sources:
  target='CMakeFiles/flycast_libretro.dir/'+relative+'.o'
  lines=subprocess.check_output([NINJA,'-C',REFERENCE,'-t','deps',target],text=True).splitlines()
  if not lines or '(VALID)' not in lines[0]:raise RuntimeError('Missing original header dependency record: '+relative)
  for line in lines[1:]:
   if not line.startswith('    '):continue
   path=Path(line.strip())
   if not path.is_absolute():path=REFERENCE/path
   path=path.resolve()
   if not path.is_relative_to(ROOT):continue
   digest=sha(path)
   if path.is_relative_to(SOURCE) and expected.get(path)!=digest:raise RuntimeError('Modified original header: '+display(path))
   pins[path]=digest
 return pins
def build(cpu,generated,output,jobs=4):
 generation=json.loads((generated/'generation.json').read_text())
 pins={ROOT/'scripts'/('compile_'+cpu+'.py'):generation['generatorSHA256']}
 pins.update({SOURCE/p:h for p,h in generation['sourcePins'].items()})
 pins.update({rooted(p):h for p,h in generation['inputPins'].items()})
 pins.update({generated/p:h for p,h in generation['sources'].items()})
 pins[ROOT/'Sources/Bridge/vt_fixed_fault.h']=sha(ROOT/'Sources/Bridge/vt_fixed_fault.h')
 pins[Path(__file__).resolve()]=sha(__file__)
 pins[ROOT/'Tools/ReferenceLab/build_observer.py']=sha(ROOT/'Tools/ReferenceLab/build_observer.py')
 pins[SOURCE/'core/hw/sh4/sh4_interpreter.h']=sha(SOURCE/'core/hw/sh4/sh4_interpreter.h')
 pins[REFERENCE/'build-manifest.json']=sha(REFERENCE/'build-manifest.json')
 pins[generated/'generation.json']=sha(generated/'generation.json')
 pins.update(dependency_pins(generation['replaces']))
 if cpu=='aicadsp':pins[ROOT/'scripts/build_aicadsp.py']=sha(ROOT/'scripts/build_aicadsp.py')
 for p,h in pins.items():
  if sha(p)!=h:raise RuntimeError('Stale fixed sound input: '+display(p))
 target='CMakeFiles/flycast_libretro.dir/'+generation['replaces'][0]+'.o'
 cmd=shlex.split(subprocess.check_output([NINJA,'-C',REFERENCE,'-t','commands',target],text=True).splitlines()[-1])
 if '-DTARGET_NO_REC' not in cmd:raise RuntimeError('Original CPU build mode changed')
 original_parent=(SOURCE/generation['replaces'][0]).parent
 output.mkdir(parents=True,exist_ok=True)
 records={};work=[]
 for source in sorted(generated.glob('*.cpp')):
  obj=output/(source.stem+'.o');args=cmd.copy()
  for flag in ('-o','-MT','-MF'):
   args[args.index(flag)+1]=str(obj)+('.d' if flag=='-MF' else '')
  args[args.index('-c')+1]=str(source)
  args[1:1]=['-iquote',str(original_parent),'-I'+str(generated),'-I'+str(ROOT/'Sources/Bridge')]
  key=display(obj);records[key]={'source':display(source),'command':args}
  work.append((args,obj))
 def compile(job):
  args,obj=job
  with obj.with_suffix('.log').open('w') as log:subprocess.run(args,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
 start=time.time()
 with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:list(pool.map(compile,work))
 for p,h in pins.items():
  if sha(p)!=h:raise RuntimeError('Fixed sound source changed during compilation: '+display(p))
 objects={display(obj):sha(obj) for _,obj in work}
 forbidden=['aica::arm::recompiler::interpret','aica::arm::runInterpreter'] if cpu=='arm7' else ['aica::dsp::DecodeInst']
 symbols=subprocess.check_output(['nm','-C',*[str(obj) for _,obj in work]],text=True)
 for name in forbidden:
  if name in symbols:raise RuntimeError('Original runtime decoder remains: '+name)
 report={'cpu':cpu,'sourcePins':{display(p):h for p,h in pins.items()},'generationSHA256':sha(generated/'generation.json'),'generationManifest':display(generated/'generation.json'),
  'replacements':{target:list(objects)},'removedOriginalObjects':['CMakeFiles/flycast_libretro.dir/'+p+'.o' for p in generation['replaces'][1:]],
  'objects':objects,'compileCommands':records,'forbiddenSymbols':forbidden,
  'requiredSymbols':['vt_fixed_'+cpu+'_marker'],'originalTarget':target,'elapsedSeconds':round(time.time()-start,3)}
 save(output/'manifest.json',report)
 print(json.dumps({'cpu':cpu,'objects':len(objects),'manifest':display(output/'manifest.json')}))
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generated',type=Path,default=ROOT/'build/generated/arm7');p.add_argument('--output',type=Path,default=ROOT/'build/native/cpu/arm7');p.add_argument('--jobs',type=int,default=4);a=p.parse_args()
 if a.jobs<1:p.error('positive job count required')
 build('arm7',a.generated.resolve(),a.output.resolve(),a.jobs)
