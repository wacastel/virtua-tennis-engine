"""Strict canonical SH4 fixture inputs, with no development-candidate dependency."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib,json
from build_observer import SOURCE,REFERENCE

def sha(path:Path)->str:
    with path.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def common_fixed_object(path:Path)->bool:
    return path.name.startswith(('vt_operations_','vt_image_','vt_sh4_dispatch'))

@dataclass(frozen=True)
class CanonicalInputs:
    root:Path
    cpu_manifest:Path
    generated_manifest:Path
    cpu:dict
    normalized:dict
    plan:Path
    include_directory:Path
    executor_source:Path
    executor_object:Path
    run_header:Path
    derived_addrspace_source:Path
    input_pins:dict

def canonical_inputs(root:Path,cpu_manifest:Path)->CanonicalInputs:
    root=root.resolve();cpu_manifest=cpu_manifest.resolve()
    def local(path:Path)->str:return str(path.resolve().relative_to(root))
    def resolve(name:str)->Path:
        p=Path(name)
        if p.is_absolute()or '..'in p.parts:raise RuntimeError('Canonical manifest path must be project-relative: '+name)
        path=(root/p).resolve();path.relative_to(root);return path
    cpu=json.loads(cpu_manifest.read_text());opt=cpu.get('runOptimization')
    if cpu.get('cpu')!='SH4':raise RuntimeError('Expected the SH4 CPU manifest')
    if not isinstance(opt,dict):raise RuntimeError('Canonical CPU lacks runOptimization metadata')
    if cpu.get('runtimeOpcodeDecoder')is not False:raise RuntimeError('CPU boundary does not exclude runtime decoding')
    pins={local(cpu_manifest):sha(cpu_manifest)}
    source_pins=cpu.get('sourcePins')
    if not isinstance(source_pins,dict)or not source_pins:raise RuntimeError('Canonical flattened sourcePins are required')
    for name,digest in source_pins.items():
        p=resolve(name)
        if not isinstance(digest,str)or sha(p)!=digest:raise RuntimeError('Canonical source changed: '+name)
        pins[name]=digest
    objects={}
    for name,digest in cpu['objects'].items():
        p=resolve(name)
        if sha(p)!=digest:raise RuntimeError('Canonical object changed: '+name)
        pins[name]=digest;objects[p]=digest
    generated=resolve(opt.get('generatedManifest','build/generated/sh4/manifest.json'))
    if sha(generated)!=cpu['generatedManifestSHA256']:raise RuntimeError('Canonical generated manifest is stale')
    pins[local(generated)]=sha(generated);generation=json.loads(generated.read_text())
    for name,digest in generation['generated'].items():
        p=(generated.parent/name).resolve();p.relative_to(root)
        if sha(p)!=digest:raise RuntimeError('Generated source changed: '+name)
        if source_pins.get(local(p))!=digest:raise RuntimeError('Generated source absent from native sourcePins: '+name)
        pins[local(p)]=digest
    def role(key:str)->Path:
        p=resolve(opt[key])
        if local(p)not in source_pins:raise RuntimeError('Unbound canonical role: '+key)
        return p
    plan=role('plan');executor=role('executorSource');run_header=role('runDispatchHeader');mapped_header=role('mappedFetchHeader');derived=role('derivedAddressSpaceSource')
    if 'mappedRead32Header'in opt:role('mappedRead32Header')
    for optional_role in ['counterLoopHeader','counterLoopSource','pureSegmentsHeader']:
        if optional_role in opt:role(optional_role)
    if len({executor.parent,run_header.parent,mapped_header.parent,derived.parent})!=1:raise RuntimeError('Canonical fixture headers require one generated directory')
    mapping=dict(opt['mappedFetch']);original_target=mapping['originalObjectTarget']
    if original_target!='CMakeFiles/flycast_libretro.dir/core/hw/mem/addrspace.cpp.o':raise RuntimeError('Unexpected mapped source replacement')
    replacement=resolve(mapping['replacementObject'])
    if replacement not in objects or cpu['replacements'].get(original_target)!=[local(replacement)]:raise RuntimeError('Mapped object not bound to actual replacement')
    interpreter_target='CMakeFiles/flycast_libretro.dir/core/hw/sh4/interpr/sh4_interpreter.cpp.o'
    interpreter_objects={resolve(p)for p in cpu['replacements'][interpreter_target]}
    if not interpreter_objects.issubset(objects):raise RuntimeError('Unbound CPU replacement object')
    executor_object=next((p for p in interpreter_objects if p.name==executor.stem+'.o'),None)
    if executor_object is None:raise RuntimeError('Cannot identify actual canonical executor object')
    if set(objects)!=interpreter_objects|{replacement}:raise RuntimeError('Unexpected CPU object inventory')
    original_source=SOURCE/'core/hw/mem/addrspace.cpp';original_object=REFERENCE/original_target
    if source_pins.get(local(original_source))!=sha(original_source):raise RuntimeError('Original address-space source not pinned')
    # The unchanged reference object is an oracle input, not a production file.
    pins[local(original_object)]=sha(original_object)
    for p in [REFERENCE/'build-manifest.json',REFERENCE/'flycast_libretro.dylib']:
        pins[local(p)]=sha(p)
    ref=json.loads((REFERENCE/'build-manifest.json').read_text())
    if sha(REFERENCE/'flycast_libretro.dylib')!=ref['productSHA256']:raise RuntimeError('Reference library changed')
    mapping.update(originalSource=local(original_source),originalSourceSHA256=sha(original_source),originalObjectSHA256=sha(original_object),replacementObject=str(replacement))
    optimized={str(p):d for p,d in objects.items()if not common_fixed_object(p)and p.name!='vt_sh4_cycles.o'}
    normalized={'identityKind':'canonical native CPU manifest','parentCPUManifestSHA256':sha(cpu_manifest),'planSHA256':sha(plan),
        'sources':{str(root/name):digest for name,digest in source_pins.items()},'objects':optimized,'mappedFetch':mapping}
    return CanonicalInputs(root,cpu_manifest,generated,cpu,normalized,plan,executor.parent,executor,executor_object,run_header,derived,pins)

def add_fixture_arguments(parser,root:Path):
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--canonical',action='store_true',help='Use the final canonical CPU manifest (default).')
    mode.add_argument('--candidate',type=Path,help='Use an isolated historical candidate; never required by the canonical build.')
    parser.add_argument('--cpu-manifest',type=Path,default=root/'build/native/cpu/sh4/manifest.json')
    parser.add_argument('--output',type=Path,required=True,help='Fresh output directory; existing evidence is never overwritten.')

def fixture_inputs(root:Path,args)->CanonicalInputs:
    if args.candidate is None:
        result=canonical_inputs(root,args.cpu_manifest)
    else:
        root=root.resolve();directory=args.candidate.resolve();manifest=directory/'manifest.json';cpu_manifest=args.cpu_manifest.resolve()
        cm=json.loads(manifest.read_text());cpu=json.loads(cpu_manifest.read_text())
        def local(p):return str(p.resolve().relative_to(root))
        pins={local(manifest):sha(manifest),local(cpu_manifest):sha(cpu_manifest)}
        if sha(cpu_manifest)!=cm['parentCPUManifestSHA256']:raise RuntimeError('Historical candidate fixed-operation parent changed')
        normalized=dict(cm)
        for group in ['sources','objects']:
            normalized[group]={}
            for name,digest in cm[group].items():
                p=(directory/name).resolve()
                if sha(p)!=digest:raise RuntimeError('Historical candidate input changed: '+name)
                pins[local(p)]=digest;normalized[group][str(p)]=digest
        for name,digest in cpu['objects'].items():
            p=root/name
            if sha(p)!=digest:raise RuntimeError('Historical candidate fixed object changed: '+name)
            pins[local(p)]=digest
        # Composite prototype engines may carry the separately tested ARM dispatcher.
        # Hash it above, but retain the original ARM hardware in this SH4-only oracle.
        arm=cm.get('combinedARM7')
        if arm:
            if arm.get('originalObject')!='build/native/cpu/arm7/arm7_dispatch.o' or arm.get('replacementObject')!='arm7_dispatch.o' or cm['objects'].get(arm['replacementObject'])!=arm.get('replacementSHA256'):
                raise RuntimeError('Unrecognized composite ARM component')
            omitted=str((directory/arm['replacementObject']).resolve())
            if omitted not in normalized['objects']:raise RuntimeError('Composite ARM object is absent')
            del normalized['objects'][omitted]
            normalized['unrelatedCompositeObjectOmitted']=arm['replacementObject']
        elif any(Path(name).name=='arm7_dispatch.o' for name in cm['objects']):
            raise RuntimeError('Unbound composite ARM object')
        mapping=dict(cm['mappedFetch']);mapping['replacementObject']=str((directory/mapping['replacementObject']).resolve())
        normalized['mappedFetch']=mapping;normalized['identityKind']='isolated historical candidate'
        plan=directory/'plan.json'
        if sha(plan)!=cm['planSHA256']:raise RuntimeError('Historical plan changed')
        pins[local(plan)]=sha(plan)
        result=CanonicalInputs(root,cpu_manifest,root/'build/generated/sh4/manifest.json',cpu,normalized,plan,directory,
            directory/'executor.cpp',directory/'executor.o',directory/'run_dispatch.h',directory/'addrspace.cpp',pins)
    result.input_pins[str(Path(__file__).resolve().relative_to(root.resolve()))]=sha(Path(__file__))
    return result
