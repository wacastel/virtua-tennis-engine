#!/usr/bin/env python3
"""Build the exact-program AICA DSP replacement with original hardware flags."""
import argparse
from pathlib import Path
from build_arm7 import ROOT,build
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generated',type=Path,default=ROOT/'build/generated/aicadsp');p.add_argument('--output',type=Path,default=ROOT/'build/native/cpu/aicadsp');p.add_argument('--jobs',type=int,default=4);a=p.parse_args()
 if a.jobs<1:p.error('positive job count required')
 build('aicadsp',a.generated.resolve(),a.output.resolve(),a.jobs)
