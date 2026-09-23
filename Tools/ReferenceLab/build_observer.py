#!/usr/bin/env python3
"""Link a separate original-CPU core with read-only execution observations."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, shlex, subprocess
from pathlib import Path
from sh4_observer import sh4_patches
from sound_observer import sound_patches

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'build/clock-source/flycast'
REFERENCE = ROOT/'build/reference-clock'
NINJA = ROOT/'build/tooling/bin/ninja'

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def command(target):
    return subprocess.check_output([NINJA, '-C', REFERENCE, '-t', 'commands', target], text=True).splitlines()[-1]

def compile_command(relative, source, output):
    target = 'CMakeFiles/flycast_libretro.dir/'+relative+'.o'
    args = shlex.split(command(target))
    for flag in ('-o','-MF','-MT'):
        index = args.index(flag)
        args[index+1] = str(output) + ('.d' if flag == '-MF' else '')
    index = args.index('-c')
    args[index+1] = str(source)
    args[1:1] = ['-iquote', str((SOURCE/relative).parent)]
    if '-DTARGET_NO_REC' not in args:
        raise RuntimeError('Reference is not configured with TARGET_NO_REC')
    return args, target

def link_command(replacements, product):
    text = command('flycast_libretro.dylib')
    if not text.startswith(': && ') or not text.endswith(' && :'):
        raise RuntimeError('Unexpected pinned Ninja link command')
    args = shlex.split(text[len(': && '):-len(' && :')])
    for old, new in replacements.items():
        if args.count(old) != 1:
            raise RuntimeError('Missing or duplicate original CPU object: '+old)
        args[args.index(old)] = str(new)
    args[args.index('-o')+1] = str(product)
    args[args.index('-install_name')+1] = '@rpath/'+product.name
    return args

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/observer-clock')
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    before = sha(REFERENCE/'flycast_libretro.dylib')
    reference_manifest = json.loads((REFERENCE/'build-manifest.json').read_text())
    if before != reference_manifest['productSHA256']:
        raise RuntimeError('Reference library identity mismatch')
    patches = sh4_patches(SOURCE) | sound_patches(SOURCE)
    records, replacements, jobs = {}, {}, []
    for relative, text in patches.items():
        source = output/'source'/relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text)
        obj = output/'objects'/(Path(relative).name+'.o')
        obj.parent.mkdir(parents=True, exist_ok=True)
        cmd, target = compile_command(relative, source, obj)
        replacements[target] = obj
        records[relative] = {'originalSHA256':sha(SOURCE/relative), 'derivedSHA256':sha(source), 'command':cmd}
        jobs.append((cmd, obj))
    def run(job):
        cmd, obj = job
        with obj.with_suffix('.log').open('w') as log:
            subprocess.run(cmd, cwd=REFERENCE, stdout=log, stderr=subprocess.STDOUT, check=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(run, jobs))
    product = output/'flycast_observer.dylib'
    cmd = link_command(replacements, product)
    with (output/'link.log').open('w') as log:
        subprocess.run(cmd, cwd=REFERENCE, stdout=log, stderr=subprocess.STDOUT, check=True)
    if sha(REFERENCE/'flycast_libretro.dylib') != before:
        raise RuntimeError('Reference changed while linking observer')
    report = {'purpose':'Development-only original CPU observer; no translated execution',
        'referenceSHA256':before, 'referenceManifestSHA256':sha(REFERENCE/'build-manifest.json'),
        'productSHA256':sha(product), 'patches':records,
        'scripts':{p.name:sha(p) for p in (Path(__file__),Path(__file__).with_name('sh4_observer.py'),Path(__file__).with_name('sound_observer.py'))},
        'objects':{str(p.relative_to(output)):sha(p) for p in replacements.values()}, 'linkCommand':cmd}
    (output/'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True).replace(str(ROOT),'PROJECT')+'\n')
    print(json.dumps({'product':str(product),'sha256':sha(product)}))

if __name__ == '__main__':
    main()
