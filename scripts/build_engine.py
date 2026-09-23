#!/usr/bin/env python3
"""Link audited fixed CPU objects into pinned NAOMI hardware, or isolated hybrids."""
from __future__ import annotations
import argparse,hashlib,json,re,shlex,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tools/ReferenceLab'))
from build_observer import SOURCE,REFERENCE,compile_command,command,sha
from native_provenance import (Snapshot, audit_cpu_inputs, validate_link_inventory,
                               verify_snapshot, minimum_macos, link_input_files)
ALL={'sh4','arm7','aicadsp'}
PREFIX='CMakeFiles/flycast_libretro.dir/'
REQUIRED_REMOVALS={
 'sh4':['core/hw/sh4/interpr/sh4_interpreter.cpp','core/hw/sh4/interpr/sh4_opcodes.cpp',
        'core/hw/sh4/interpr/sh4_fpu.cpp','core/hw/sh4/sh4_opcode_list.cpp','core/hw/sh4/sh4_cycles.cpp'],
 'arm7':['core/hw/arm7/arm7.cpp'],
 'aicadsp':['core/hw/aica/dsp.cpp','core/hw/aica/dsp_interp.cpp']}
BUILTIN_BANS={'sh4':['OpPtr','OpDesc','BuildOpcodeTables','Sh4Cycles::countCycles'],
 'arm7':['aica::arm::recompiler::interpret','aica::arm::runInterpreter'],
 'aicadsp':['aica::dsp::DecodeInst']}

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--cpus',default='sh4,arm7,aicadsp')
 p.add_argument('--output',type=Path)
 p.add_argument('--diagnostic',action='store_true')
 a=p.parse_args();cpus=set(a.cpus.split(','))
 if not cpus or not cpus<=ALL:p.error('Unknown CPU selection')
 shipping=cpus==ALL and not a.diagnostic
 out=(a.output or ROOT/('build/native' if shipping else 'build/hybrid-'+ '-'.join(sorted(cpus)))).resolve()
 out.mkdir(parents=True,exist_ok=True)
 initial=Snapshot(ROOT)
 initial.add(__file__)
 provenance_helper=ROOT/'scripts/native_provenance.py';initial.add(provenance_helper)
 initial.add(ROOT/'Tools/ReferenceLab/build_observer.py')
 original=REFERENCE/'flycast_libretro.dylib';original_sha=sha(original);initial.add(original,original_sha)
 reference_manifest=REFERENCE/'build-manifest.json'
 reference_info=initial.read_json(reference_manifest)
 if reference_info['productSHA256']!=original_sha:raise RuntimeError('Reference library changed')
 if reference_info.get('interpreterCycleMultiplier')!=1 or reference_info.get('sh4ClockHz')!=200000000:
  raise RuntimeError('The shipping hardware requires the documented 200 MHz SH4 clock')
 audit=audit_cpu_inputs(ROOT,cpus)
 hashes=audit['CPUManifests'];objects=audit['objects'];replacements=audit['replacements'];removals=audit['removals']
 forbidden=sorted(set(audit['forbidden']+[b for cpu in cpus for b in BUILTIN_BANS[cpu]]))
 line=command('flycast_libretro.dylib')
 if not line.startswith(': && ') or not line.endswith(' && :'):raise RuntimeError('Unrecognized original link recipe')
 args=shlex.split(line[5:-5])
 for old in list(replacements)+removals:
  if args.count(old)!=1:raise RuntimeError('Expected exactly one original CPU object: '+old)
 linked=[]
 for arg in args:
  if arg in replacements:linked.extend(replacements[arg])
  elif arg not in removals and arg!='-s':linked.append(arg)
 for cpu in cpus:
  for relative in REQUIRED_REMOVALS[cpu]:
   if PREFIX+relative+'.o' in linked:raise RuntimeError('Original '+cpu+' execution object remains: '+relative)
 # These source files are unused under TARGET_NO_REC. Excluding their objects
 # makes the no-JIT boundary explicit instead of relying on dead stripping.
 jit_objects=[x for x in linked if x.endswith('.o') and any(s in x for s in (
  '/core/hw/sh4/dyna/','/core/rec-','/core/deps/vixl/','/core/hw/arm7/arm7_rec',
  '/core/hw/aica/dsp_arm','/core/hw/aica/dsp_x86','/core/hw/aica/dsp_x64'))]
 linked=[x for x in linked if x not in jit_objects]
 order_file=audit['runOptimization'].get('sh4',{}).get('linkOrderFile')
 if order_file:
  linked.append('-Wl,-order_file,'+str(initial.add(order_file)))
 validate_link_inventory(ROOT,linked,audit,REFERENCE)
 hardware_inputs=link_input_files(ROOT,linked,REFERENCE)
 common=ROOT/'Sources/Bridge/vt_fixed_fault.cpp';common_obj=out/'vt_fixed_fault.o'
 initial.add(common);initial.add(common.with_suffix('.h'))
 compile_args,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',common,common_obj)
 compile_args[1:1]=['-I'+str(common.parent)]+(['-DVT_FIXED_NATIVE'] if shipping else [])
 with (out/'common-build.log').open('w') as log:subprocess.run(compile_args,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT,check=True)
 linked.extend([str(common_obj),'-Wl,-dead_strip'])
 bridge_sources={}
 if shipping:
  bridge=ROOT/'Sources/Bridge/virtua_tennis.mm';bridge_obj=out/'virtua_tennis_bridge.o'
  for f in [bridge,bridge.with_suffix('.h'),bridge.parent/'media_identity.h']:initial.add(f)
  bridge_args,_=compile_command('core/hw/sh4/interpr/sh4_interpreter.cpp',bridge,bridge_obj)
  bridge_args[1:1]=['-DVT_FIXED_NATIVE','-DGL_SILENCE_DEPRECATION','-fobjc-arc','-I'+str(SOURCE/'core/deps/libretro-common/include')]
  with (out/'bridge-build.log').open('w') as log:result=subprocess.run(bridge_args,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT)
  if result.returncode:raise RuntimeError('Bridge compile failed: '+str(out/'bridge-build.log'))
  linked.extend([str(bridge_obj),'-framework','CoreGraphics'])
  bridge_sources={str(f.relative_to(ROOT)):initial.pins[str(f.relative_to(ROOT))] for f in [bridge,bridge.with_suffix('.h'),bridge.parent/'media_identity.h']}
 exports=out/'exports.txt'
 extra=['vt_fixed_fault','vt_fixed_error','vt_fixed_clear_error','vt_fixed_ticks']
 if shipping:
  extra.append('vt_fixed_engine_marker')
  extra.extend(['vt_create','vt_destroy','vt_reset','vt_step','vt_error','vt_fault_code','vt_frame_number',
   'vt_frame_rate','vt_emulated_seconds','vt_width','vt_height','vt_aspect_ratio','vt_pixels','vt_audio','vt_audio_count','vt_audio_sample_rate'])
 extra.extend('vt_fixed_'+cpu+'_marker' for cpu in sorted(cpus))
 original_exports=initial.add(SOURCE/'shell/libretro/libretro.osx.def')
 exports.write_text(original_exports.read_text()+'\n'+'\n'.join('_'+x for x in extra)+'\n')
 initial.add(exports)
 for i,arg in enumerate(linked):
  if arg.startswith('-Wl,-exported_symbols_list,'):linked[i]='-Wl,-exported_symbols_list,'+str(exports)
 product=out/'libvirtua_tennis.dylib'
 linked[linked.index('-o')+1]=str(product);linked[linked.index('-install_name')+1]='@rpath/libvirtua_tennis.dylib'
 validate_link_inventory(ROOT,linked,audit,REFERENCE)
 verify_snapshot(ROOT,audit['inputFiles']);verify_snapshot(ROOT,initial.pins);verify_snapshot(ROOT,hardware_inputs)
 linked_inputs=link_input_files(ROOT,linked,REFERENCE)
 with (out/'link.log').open('w') as log:linked_result=subprocess.run(linked,cwd=REFERENCE,stdout=log,stderr=subprocess.STDOUT)
 if linked_result.returncode:
  raise RuntimeError('Engine link failed; '+str(out/'link.log')+'\n'+'\n'.join((out/'link.log').read_text().splitlines()[-18:]))
 symbols=subprocess.check_output(['/usr/bin/nm','-C',str(product)],text=True)
 for banned in forbidden:
  if banned in symbols:raise RuntimeError('Original CPU decode symbol remains: '+banned)
 global_symbols=subprocess.check_output(['/usr/bin/nm','-gU',str(product)],text=True)
 for name in extra:
  if not re.search(r'\b_'+re.escape(name)+r'$',global_symbols,re.M):raise RuntimeError('Required engine symbol absent: '+name)
 arch=subprocess.check_output(['lipo','-archs',str(product)],text=True).strip()
 if arch!='arm64':raise RuntimeError('Unexpected engine architecture: '+arch)
 minimum=minimum_macos(product)
 deps=subprocess.check_output(['otool','-L',str(product)],text=True)
 for dep in deps.splitlines()[1:]:
  name=dep.strip().split(' (')[0]
  if not name.startswith(('@rpath/libvirtua_tennis.dylib','/usr/lib/','/System/Library/Frameworks/')):raise RuntimeError('External runtime dependency: '+name)
 verify_snapshot(ROOT,audit['inputFiles']);verify_snapshot(ROOT,initial.pins);verify_snapshot(ROOT,linked_inputs)
 report={'shippingFixedEngine':shipping,'selectedCPUs':sorted(cpus),'originalCPUsRemaining':sorted(ALL-cpus),
  'diagnostic':a.diagnostic,'runtimeInterpreterFallback':False if cpus==ALL else 'Original unselected CPUs remain in this laboratory hybrid',
  'architecture':arch,'minimumMacOS':minimum,'referenceSHA256':original_sha,
  'sh4ClockHz':200000000,'interpreterCycleMultiplier':1,
  'referenceManifestSHA256':sha(reference_manifest),'CPUManifests':hashes,
  'CPUInputFiles':audit['inputFiles'],'runOptimization':audit['runOptimization'],
  'provenanceHelperSHA256':initial.pins[str(provenance_helper.relative_to(ROOT))],
  'buildInputFiles':initial.pins,
  'linkedInputFiles':linked_inputs,
  'originalObjectsReplaced':replacements,'originalObjectsRemoved':removals,
  'JITObjectsExcluded':jit_objects,'bridgeSources':bridge_sources,
  'replacementObjects':objects,'commonObjectSHA256':sha(common_obj),
  'product':str(product.relative_to(ROOT)),'productSHA256':sha(product),
  'scriptSHA256':sha(__file__),'commonSourceSHA256':sha(common),'commonHeaderSHA256':sha(common.with_suffix('.h')),
  'sourceChanges':'All CPU transformations are generated in isolated build paths; original hardware source retained.',
  'linkCommand':linked,'dynamicDependencies':deps.splitlines()[1:],'forbiddenSymbolsAbsent':forbidden}
 (out/'manifest.json').write_text(json.dumps(report,indent=2,sort_keys=True).replace(str(ROOT),'PROJECT')+'\n')
 (out/'symbols.txt').write_text(symbols)
 print(json.dumps({'product':str(product),'sha256':sha(product),'shippingFixedEngine':shipping,'CPUs':sorted(cpus)}))
if __name__=='__main__':main()
