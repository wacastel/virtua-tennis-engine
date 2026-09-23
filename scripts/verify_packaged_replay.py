#!/usr/bin/env python3
"""Compare a finished app's C ABI/Swift replay against the original CPU laboratory."""
from pathlib import Path
import argparse,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--reference',required=True,type=Path)
 p.add_argument('--report',required=True,type=Path)
 p.add_argument('--frames',required=True,type=Path)
 p.add_argument('--output',required=True,type=Path)
 a=p.parse_args();original=json.loads((a.reference/'report.json').read_text());app=json.loads(a.report.read_text())
 reference=[json.loads(s) for s in (a.reference/'frames.jsonl').read_text().splitlines()]
 candidate=[json.loads(s) for s in a.frames.read_text().splitlines()]
 if len(candidate)!=len(reference) or len(candidate)!=app['frames'] or len(reference)!=original['frames']:
  raise RuntimeError('Incomplete or unequal replay lengths')
 differences=[]
 for index,(r,c) in enumerate(zip(reference,candidate)):
  pairs={'frame':(index+1,c['frame']),'originalSequence':(index+1,r['frame']),
   'pixels':(r['rgbaSHA256'],c['rgba']),'samples':(r['pcmSHA256'],c['pcm']),
   'audioFrames':(r['audioFrames'],c['audioFrames']),
   # The C ABI expresses an integer scheduler tick count as seconds. Restore
   # those 5 ns ticks after Swift's decimal JSON round trip (a few double ULPs).
   'schedulerTicks':(r['sh4Ticks'],round(c['emulatedSeconds']*200000000))}
  diff={k:list(v) for k,v in pairs.items() if v[0]!=v[1]}
  if diff:differences.append({'step':index+1,'differences':diff})
  if len(differences)==10:break
 if app['sampleFrames']!=original['stereoSampleFrames'] or app['audioSHA256']!=original['pcmStreamSHA256']:
  raise RuntimeError('Packaged stream/audio count differs from original')
 package=json.loads((ROOT/'build/package/manifest.json').read_text())
 for rel,entry in package['artifacts'].items():
  if sha(ROOT/'build/Virtua Tennis.app'/rel)!=entry['sha256']:raise RuntimeError('Package changed during verification')
 result={'passed':not differences,'steps':len(candidate),'stereoSampleFrames':app['sampleFrames'],
  'emulatedSeconds':app['emulatedSeconds'],'exactRGBA_PCM_Counts_Clock':not differences,
  'clockComparison':'Integer 200MHz ticks reconstructed by rounding C ABI double seconds after JSON serialization',
  'scriptSHA256':sha(Path(__file__)),'originalReportSHA256':sha(a.reference/'report.json'),
  'appReportSHA256':sha(a.report),'appFramesSHA256':sha(a.frames),
  'packageManifestSHA256':sha(ROOT/'build/package/manifest.json'),'packageArtifacts':package['artifacts'],
  'firstDifferences':differences,'scope':'Per-step equality through the actual packaged Swift host and C ABI, with fixed RTC and the same authored digital input route. This does not establish live audio pacing or physical controller actuation.'}
 a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
 print(json.dumps({k:result[k] for k in ['passed','steps','stereoSampleFrames','firstDifferences']}))
 if differences:raise SystemExit(1)
if __name__=='__main__':main()
