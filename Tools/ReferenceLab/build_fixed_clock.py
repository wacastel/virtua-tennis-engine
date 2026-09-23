#!/usr/bin/env python3
"""Build and verify a laboratory-only fixed RTC seed provider for paired runs."""
from pathlib import Path
import hashlib,json,os,subprocess
ROOT=Path(__file__).resolve().parents[2]
source=Path(__file__).with_name('fixed_clock.c')
out=ROOT/'build/reference-lab';out.mkdir(parents=True,exist_ok=True)
lib=out/'fixed-clock.dylib'
flags=['xcrun','clang','-O2','-arch','arm64','-mmacosx-version-min=14.0']
subprocess.run(flags+['-dynamiclib',str(source),'-o',str(lib)],check=True)
probe=out/'clock-probe.c'
probe.write_text('#include <time.h>\n#include <stdio.h>\nint main(void){time_t a,b;a=time(&b);printf("%lld %lld\\n",(long long)a,(long long)b);return a!=b;}\n')
subprocess.run(flags+[str(probe),'-o',str(out/'clock-probe')],check=True)
epoch='946684800';env=dict(os.environ,DYLD_INSERT_LIBRARIES=str(lib),VT_TEST_EPOCH=epoch,TZ='UTC')
actual=subprocess.check_output([str(out/'clock-probe')],env=env,text=True).strip()
assert actual==epoch+' '+epoch,actual
report={'passed':True,'scope':'Host time() RTC seed only, no scheduler/gameplay timer modifications',
 'testEpochUnixSeconds':int(epoch),'timezone':'UTC','sourceSHA256':hashlib.sha256(source.read_bytes()).hexdigest(),
 'librarySHA256':hashlib.sha256(lib.read_bytes()).hexdigest(),'shipsInGame':False}
(out/'fixed-clock-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
