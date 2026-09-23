#!/usr/bin/env python3
"""Compare completed same-input laboratory runs exactly at every frontend boundary."""
from pathlib import Path
import argparse,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def relative(p):
 try:return str(p.resolve().relative_to(ROOT))
 except ValueError:return p.name

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--reference',required=True,type=Path);p.add_argument('--candidate',required=True,type=Path)
 p.add_argument('--output',required=True,type=Path);p.add_argument('--purpose',default='Fixed/reference replay comparison')
 a=p.parse_args();reports=[];rows=[]
 for folder in [a.reference,a.candidate]:
  report=json.loads((folder/'report.json').read_text());data=[json.loads(s) for s in (folder/'frames.jsonl').read_text().splitlines()]
  if len(data)!=report['frames'] or not data:raise RuntimeError('Incomplete run')
  if not all(r['frame']==i+1 for i,r in enumerate(data)):raise RuntimeError('Discontinuous steps')
  if sum(r['audioFrames'] for r in data)!=report['stereoSampleFrames']:raise RuntimeError('Audio count mismatch')
  reports.append(report);rows.append(data)
 for key in ['frames','contentSHA256','routeSHA256','coreOptions','sampleRate','width','height','harnessSHA256','schedulerTicksAvailable']:
  if reports[0][key]!=reports[1][key]:raise RuntimeError('Different comparison inputs/configuration: '+key)
 if not reports[0]['schedulerTicksAvailable']:raise RuntimeError('Hardware clock evidence is required')
 differences=[]
 for x,y in zip(*rows):
  if x!=y:
   differences.append({'step':x['frame'],'fields':{k:[x.get(k),y.get(k)] for k in x.keys()|y.keys() if x.get(k)!=y.get(k)}})
   if len(differences)==10:break
 result={'passed':not differences,'purpose':a.purpose,'stepUnit':reports[0]['stepUnit'],
  'comparedSteps':len(rows[0]),'exactRGBA':not differences,'exactPCMAndCounts':not differences,
  'exactInputAndHardwareClock':not differences,'stereoSampleFrames':reports[0]['stereoSampleFrames'],
  'finalSH4Ticks':rows[0][-1]['sh4Ticks'],'emulatedSeconds':rows[0][-1]['sh4Ticks']/200000000,
  'firstDifferences':differences,'sourceScriptSHA256':sha(Path(__file__)),
  'runs':[{'path':relative(folder),'reportSHA256':sha(folder/'report.json'),
    'frameStreamSHA256':sha(folder/'frames.jsonl'),'coreSHA256':report['coreSHA256'],
    'harnessSHA256':report['harnessSHA256'],'contentSHA256':report['contentSHA256'],
    'routeSHA256':report['routeSHA256'],'pcmStreamSHA256':report['pcmStreamSHA256'],
    'finalRGBA_SHA256':report['rgbaSHA256']} for folder,report in zip([a.reference,a.candidate],reports)],
  'scope':'Exact pixels, samples/counts, inputs and scheduler ticks for this authentic replay on this host. This does not prove unvisited paths, all players or physical-board equivalence.'}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
 print(json.dumps({k:result[k] for k in ['passed','comparedSteps','stereoSampleFrames','emulatedSeconds','firstDifferences']}))
 if differences:raise SystemExit(1)
if __name__=='__main__':main()
