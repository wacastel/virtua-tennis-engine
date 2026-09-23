#!/usr/bin/env python3
"""Exercise the actual fixed engine's media boundary, lifecycle and fault recovery."""
from pathlib import Path
import argparse,ctypes,hashlib,json,shutil,threading,time

ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--engine',type=Path,default=ROOT/'build/native/libvirtua_tennis.dylib')
 p.add_argument('--output',type=Path,default=ROOT/'build/verification/bridge')
 a=p.parse_args();out=a.output.resolve()
 if out.exists():raise RuntimeError('Use a fresh verification output directory')
 out.mkdir(parents=True);assets=ROOT/'build/assets'
 engine=ctypes.CDLL(str(a.engine.resolve()))
 signatures={'vt_create':(ctypes.c_void_p,[ctypes.c_char_p,ctypes.c_char_p]),
  'vt_destroy':(None,[ctypes.c_void_p]),'vt_reset':(ctypes.c_int,[ctypes.c_void_p]),
  'vt_step':(ctypes.c_int,[ctypes.c_void_p,ctypes.c_uint32]),
  'vt_error':(ctypes.c_char_p,[ctypes.c_void_p]),'vt_fault_code':(ctypes.c_uint32,[ctypes.c_void_p]),
  'vt_frame_number':(ctypes.c_uint64,[ctypes.c_void_p]),'vt_emulated_seconds':(ctypes.c_double,[ctypes.c_void_p]),
  'vt_audio_sample_rate':(ctypes.c_uint32,[ctypes.c_void_p]),'vt_width':(ctypes.c_int,[ctypes.c_void_p]),
  'vt_height':(ctypes.c_int,[ctypes.c_void_p]),'vt_fixed_engine_marker':(ctypes.c_uint32,[])}
 for name,(restype,argtypes) in signatures.items():
  function=getattr(engine,name);function.restype=restype;function.argtypes=argtypes
 checks=[]
 def check(name,ok):
  if not ok:raise RuntimeError(name+': '+str(engine.vt_error(None)))
  checks.append(name)
 def create(media,saves):return engine.vt_create(str(media).encode(),str(saves).encode())
 identities={str(f.relative_to(assets)):sha(f) for f in [assets/'vtennis.zip',assets/'system/dc/naomi.zip']}
 check('shipping marker',engine.vt_fixed_engine_marker()==0x56544658)
 check('missing media rejected',not create(out/'missing',out/'missing-saves') and b'missing' in engine.vt_error(None).lower())
 bad=out/'bad-media';(bad/'system/dc').mkdir(parents=True)
 # Copy on write where available; never mutate supplied/staged original media.
 shutil.copy2(assets/'vtennis.zip',bad/'vtennis.zip')
 shutil.copy2(assets/'system/dc/naomi.zip',bad/'system/dc/naomi.zip')
 with (bad/'vtennis.zip').open('r+b') as f:
  f.seek(4096);old=f.read(1);f.seek(4096);f.write(bytes([old[0]^1]))
 check('same-length altered media rejected',not create(bad,out/'bad-saves') and b'identity' in engine.vt_error(None))
 check('bundle writes rejected',not create(assets,assets/'test-forbidden-saves') and b'outside' in engine.vt_error(None))
 context=create(assets,out/'saves');check('real engine creation',bool(context))
 try:
  check('640x480 stereo format',engine.vt_width(context)==640 and engine.vt_height(context)==480 and engine.vt_audio_sample_rate(context)==44100)
  check('second session rejected',not create(assets,out/'second-saves') and b'one' in engine.vt_error(None))
  for _ in range(24):check('initial step '+str(_+1),engine.vt_step(context,0)==1)
  check('hardware time advances',engine.vt_emulated_seconds(context)>0 and engine.vt_frame_number(context)==24)
  check('invalid input fails closed',engine.vt_step(context,1<<31)==0 and engine.vt_fault_code(context)!=0)
  check('fault is sticky',engine.vt_step(context,0)==0)
  check('reset recovers input fault',engine.vt_reset(context)==1 and engine.vt_frame_number(context)==0 and engine.vt_fault_code(context)==0)
  result=[]
  worker=threading.Thread(target=lambda:result.append(engine.vt_step(context,0)));worker.start();worker.join()
  check('foreign thread rejected',result==[0] and b'creating thread' in engine.vt_error(context))
  check('reset recovers ownership fault',engine.vt_reset(context)==1)
  for _ in range(24):check('recovered step '+str(_+1),engine.vt_step(context,0)==1)
 finally:engine.vt_destroy(context)
 check('closed session rejected',engine.vt_step(context,0)==0)
 engine.vt_destroy(context)
 check('staged media unchanged',all(sha(assets/name)==digest for name,digest in identities.items()))
 report={'passed':True,'checks':checks,'engineSHA256':sha(a.engine),'scriptSHA256':sha(__file__),
  'mediaSHA256':identities,'scope':'Actual fixed-engine C ABI: media validation, format, 48 boot steps, singleton, sticky faults, creating-thread requirement and two resets. No gameplay/controller or full match claim.'}
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'passed':True,'checks':len(checks),'report':str(out/'report.json')}))
if __name__=='__main__':main()
