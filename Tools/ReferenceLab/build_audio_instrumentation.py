#!/usr/bin/env python3
"""Build, but never run, an explicitly instrumented copy of the playback lab."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = ('AudioOutput', 'Controls', 'FrameClock', 'FrameContinuation', 'FramePresentation', 'GameScene', 'Input', 'Media', 'NativeGame')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


TRACE_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
// Developer-only bounded main-thread metadata capture. No PCM, microphone,
// disk I/O, scheduling change or clock adjustment occurs while recording.
import Foundation
import CryptoKit

enum VTBatchTrace {
    struct Event: Encodable {
        let sequence: Int
        let kind: String
        let wallSeconds: Double
        let frame: Int
        let nativeStartSeconds: Double?
        let nativeStatus: Int32?
        let emulatedSeconds: Double?
        let enqueueArrivalSeconds: Double?
        let batchSampleFrames: Int?
        let sampleRate: Double?
        let scheduledSampleFrames: Int64?
        let renderedSampleFrames: Int64?
        let pendingSampleFrames: Int64?
        let playerStarted: Bool?
        let reason: String?
    }
    static let limit = 65_536
    private static var events: [Event] = []
    private static var attempted = 0
    private static var frame = 0
    private static var active = false
    static var complete: Bool { attempted == events.count }
    static func now() -> Double { ProcessInfo.processInfo.systemUptime }
    static func begin() { events.reserveCapacity(limit); active = true }
    static func record(_ kind: String, wall: Double? = nil, step: Int? = nil,
                       nativeStart: Double? = nil, status: Int32? = nil, emulated: Double? = nil,
                       arrival: Double? = nil, batch: Int? = nil, rate: Double? = nil,
                       scheduled: Int64? = nil, rendered: Int64? = nil,
                       pending: Int64? = nil, started: Bool? = nil, reason: String? = nil) {
        guard active else { return }
        // All instrumented sites belong to the existing main-thread producer.
        // There is deliberately no audio-thread callback/tap in this recorder.
        if let step { frame = step }
        attempted += 1
        guard events.count < limit else { return }
        events.append(Event(sequence: attempted, kind: kind, wallSeconds: wall ?? now(), frame: frame,
            nativeStartSeconds: nativeStart, nativeStatus: status, emulatedSeconds: emulated,
            enqueueArrivalSeconds: arrival, batchSampleFrames: batch, sampleRate: rate,
            scheduledSampleFrames: scheduled, renderedSampleFrames: rendered,
            pendingSampleFrames: pending, playerStarted: started, reason: reason))
    }
    static func write(to url: URL, start: Double, end: Double) throws -> [String: Any] {
        active = false
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var data = Data()
        for event in events { data.append(try encoder.encode(event)); data.append(10) }
        try data.write(to: url, options: .atomic)
        return ["file": url.lastPathComponent,
            "sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(),
            "recordLimit": limit, "recordedEvents": events.count,
            "droppedEvents": attempted - events.count, "complete": attempted == events.count,
            "measurementStartSeconds": start, "measurementEndSeconds": end,
            "clock": "ProcessInfo.systemUptime, monotonic seconds",
            "scope": "Diagnostic metadata only; in-memory bounded producer-thread recording, serialized after playback and shutdown. Instrumentation adds timestamp/append overhead. No PCM, microphone, audio tap, buffering or scheduling change."]
    }
}
'''


def derive(sources):
    """Fail closed if an instrumentation anchor changes or is not unique."""
    changes = []

    def replace(file, label, old, new):
        if sources[file].count(old) != 1:
            raise RuntimeError(f'Instrumentation anchor is absent or ambiguous: {file}: {label}')
        sources[file] = sources[file].replace(old, new)
        changes.append({'file': file, 'label': label, 'original': old, 'replacement': new})

    replace('NativeGame.swift', 'time the unchanged native step',
        '        guard nativeStep(context, input.buttons) == 1 else { throw failure }',
        '''        let traceNativeStart = VTBatchTrace.now()
        let traceNativeStatus = nativeStep(context, input.buttons)
        let traceNativeReturn = VTBatchTrace.now()
        VTBatchTrace.record("nativeReturn", wall: traceNativeReturn, step: frameCount + 1,
                            nativeStart: traceNativeStart, status: traceNativeStatus, emulated: emulatedSeconds)
        guard traceNativeStatus == 1 else { throw failure }''')
    replace('NativeGame.swift', 'record original Swift copy/validation completion',
        '        frameCount += 1; sampleFrames += count\n        return samples',
        '''        frameCount += 1; sampleFrames += count
        VTBatchTrace.record("gameReturn", step: frameCount, emulated: emulatedSeconds,
                            batch: count, rate: Double(sampleRate))
        return samples''')
    replace('AudioOutput.swift', 'record flush reason without querying another render time',
        '    private func flushLocked(reason: String) {\n        flushCounts[reason, default: 0] += 1',
        '''    private func flushLocked(reason: String) {
        VTBatchTrace.record("flush", scheduled: scheduledFrames, started: started, reason: reason)
        flushCounts[reason, default: 0] += 1''')
    replace('AudioOutput.swift', 'capture enqueue arrival before existing lock/guards',
        '    func present(_ samples: [Int16]) {\n        let count = samples.count / 2',
        '''    func present(_ samples: [Int16]) {
        let traceArrival = VTBatchTrace.now()
        let count = samples.count / 2''')
    replace('AudioOutput.swift', 'reuse existing pre-enqueue render query',
        '        if let rendered = renderedFramesLocked() {\n            let pending = scheduledFrames - rendered',
        '''        let traceRenderedBefore = renderedFramesLocked()
        VTBatchTrace.record("enqueueBefore", arrival: traceArrival, batch: count, rate: format.sampleRate,
                            scheduled: scheduledFrames, rendered: traceRenderedBefore,
                            pending: traceRenderedBefore.map { scheduledFrames - $0 }, started: started)
        if let rendered = traceRenderedBefore {
            let pending = scheduledFrames - rendered''')
    replace('AudioOutput.swift', 'reuse existing post-schedule render query',
        '        let pending = scheduledFrames - (renderedFramesLocked() ?? 0)\n        peakPendingFrames',
        '''        let traceRenderedAfter = renderedFramesLocked()
        let pending = scheduledFrames - (traceRenderedAfter ?? 0)
        peakPendingFrames''')
    replace('AudioOutput.swift', 'record scheduled end and original player-start decision',
        '''        if !started && scheduledFrames >= prebufferFrames {
            player.play()
            started = true
        }
    }''',
        '''        if !started && scheduledFrames >= prebufferFrames {
            player.play()
            started = true
            VTBatchTrace.record("playerStart", scheduled: scheduledFrames, started: started)
        }
        VTBatchTrace.record("enqueueAfter", arrival: traceArrival, batch: count, rate: format.sampleRate,
                            scheduled: scheduledFrames, rendered: traceRenderedAfter,
                            pending: pending, started: started)
    }''')
    replace('PlaybackHarness.swift', 'begin bounded recorder before scene initialization',
        '        let initializationStart = ProcessInfo.processInfo.systemUptime',
        '        VTBatchTrace.begin()\n        let initializationStart = ProcessInfo.processInfo.systemUptime')
    replace('PlaybackHarness.swift', 'mark original measured interval start',
        '        let start = ProcessInfo.processInfo.systemUptime\n        var finished = false',
        '        let start = ProcessInfo.processInfo.systemUptime\n        VTBatchTrace.record("measurementStart", wall: start)\n        var finished = false')
    replace('PlaybackHarness.swift', 'mark original measured interval end',
        '        let wallSeconds = ProcessInfo.processInfo.systemUptime - start\n        var result',
        '        let wallSeconds = ProcessInfo.processInfo.systemUptime - start\n        VTBatchTrace.record("measurementEnd", wall: start + wallSeconds)\n        var result')
    replace('PlaybackHarness.swift', 'serialize trace only after measurement/capture/shutdown',
        '        result["scheduling"] = scheduling.report\n        if let failure',
        '''        result["scheduling"] = scheduling.report
        result["batchTrace"] = try VTBatchTrace.write(to: output.appendingPathComponent("audio-batches.jsonl"),
                                                   start: start, end: start + wallSeconds)
        if let failure''')
    replace('PlaybackHarness.swift', 'require a complete bounded trace',
        '            "durationCompleted": wallSeconds >= duration,',
        '            "durationCompleted": wallSeconds >= duration,\n            "batchTraceNotTruncated": VTBatchTrace.complete,')
    return changes


def engine_identity(engine, manifest):
    m = json.loads(manifest.read_text())
    parent_path = ROOT / 'build/native/manifest.json'
    parent = json.loads(parent_path.read_text())
    if (parent.get('shippingFixedEngine') is not True or parent.get('diagnostic') is not False
            or parent.get('runtimeInterpreterFallback') is not False
            or parent.get('originalCPUsRemaining') != []
            or set(parent.get('selectedCPUs', [])) != {'sh4', 'arm7', 'aicadsp'}):
        raise RuntimeError('The parent must be a complete fixed engine, never a reference/stub')
    if sha(ROOT / 'build/native/libvirtua_tennis.dylib') != parent['productSHA256']:
        raise RuntimeError('Parent fixed engine changed')
    if m.get('productSHA256') != sha(engine):
        raise RuntimeError('Engine differs from supplied manifest')
    if manifest.resolve() != parent_path.resolve():
        if not m.get('candidate') or m.get('parentEngineSHA256') != parent['productSHA256']:
            raise RuntimeError('Experimental engine must identify its exact fixed parent')
        for group in ('sources', 'objects'):
            if not m.get(group):
                raise RuntimeError('Experimental engine must bind its sources and objects')
            for relative, expected in m[group].items():
                path = manifest.parent / relative
                if not path.resolve().is_relative_to(ROOT / 'build') or sha(path) != expected:
                    raise RuntimeError('Changed experimental engine input: ' + relative)
    symbols = subprocess.check_output(['/usr/bin/nm', '-gU', str(engine)], text=True)
    for required in ('vt_fixed_engine_marker', 'vt_create'):
        if not re.search(r'\s_' + required + r'$', symbols, re.M):
            raise RuntimeError('Required fixed-engine symbol is absent: ' + required)
    if re.search(r'\s_vt_\w*(?:reference|observer|diagnostic|test_stub)\w*marker$', symbols, re.M):
        raise RuntimeError('Refusing a reference/observer/diagnostic/stub engine')
    all_symbols = subprocess.check_output(['/usr/bin/nm', '-C', str(engine)], text=True)
    for banned in parent['forbiddenSymbolsAbsent']:
        if banned in all_symbols:
            raise RuntimeError('Original decoder remains: ' + banned)
    return {'engineManifestSHA256': sha(manifest), 'engineSHA256': sha(engine),
            'parentEngineManifestSHA256': sha(parent_path), 'experimentalEngine': manifest != parent_path}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'build/performance/audio-instrumentation')
    parser.add_argument('--engine', type=Path, default=ROOT / 'build/native/libvirtua_tennis.dylib')
    parser.add_argument('--engine-manifest', type=Path, default=ROOT / 'build/native/manifest.json')
    args = parser.parse_args()
    out, engine, engine_manifest = (p.resolve() for p in (args.out, args.engine, args.engine_manifest))
    if not out.is_relative_to(ROOT / 'build') or out.exists():
        raise RuntimeError('Output must be a new directory inside ignored build/')
    identity = engine_identity(engine, engine_manifest)
    originals = {name + '.swift': ROOT / 'Sources/Mac' / (name + '.swift') for name in SUPPORT}
    originals['PlaybackHarness.swift'] = ROOT / 'Tools/ReferenceLab/PlaybackHarness.swift'
    original_hashes = {str(p.relative_to(ROOT)): sha(p) for p in originals.values()}
    sources = {name: path.read_text() for name, path in originals.items()}
    changes = derive(sources)
    derived = out / 'sources'; derived.mkdir(parents=True)
    for name, text in sources.items():
        (derived / name).write_text(text)
    (derived / 'BatchTrace.swift').write_text(TRACE_SOURCE)
    shutil.copy2(engine, out / 'libvirtua_tennis.dylib')
    shutil.copy2(engine_manifest, out / 'engine-input-manifest.json')
    patches = out / 'replacements.json'
    patches.write_text(json.dumps(changes, indent=2) + '\n')
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    command = ['xcrun', 'swiftc', '-swift-version', '5', '-target', 'arm64-apple-macosx14.0', '-sdk', sdk, '-O']
    for framework in ('AppKit', 'SpriteKit', 'AVFoundation', 'GameController', 'CoreGraphics'):
        command += ['-framework', framework]
    command += [str(derived / name) for name in [*sources, 'BatchTrace.swift']]
    command += [str(out / 'libvirtua_tennis.dylib'), '-Xlinker', '-rpath', '-Xlinker', '@executable_path',
                '-Xlinker', '-dead_strip', '-o', str(out / 'playback-harness')]
    with (out / 'compile.log').open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    if sha(engine) != identity['engineSHA256'] or sha(out / 'libvirtua_tennis.dylib') != identity['engineSHA256']:
        raise RuntimeError('Engine changed while building')
    if {str(p.relative_to(ROOT)): sha(p) for p in originals.values()} != original_hashes:
        raise RuntimeError('Original host source changed while building')
    manifest = {**identity, 'developmentOnly': True, 'visibleWindow': False, 'appSourcesModified': False,
        'instrumentedCopies': True, 'bufferingSchedulingClockOrInputChanged': False,
        'recordLimit': 65_536, 'instrumentationOverheadUnmeasured': True, 'ran': False,
        'target': 'arm64-apple-macosx14.0', 'scriptSHA256': sha(Path(__file__)),
        'compiler': subprocess.check_output(['xcrun', 'swiftc', '--version'], text=True).strip(),
        'sources': original_hashes, 'derivedSources': {p.name: sha(p) for p in sorted(derived.glob('*.swift'))},
        'replacementsSHA256': sha(patches), 'replacementCount': len(changes),
        'executableSHA256': sha(out / 'playback-harness'),
        'buildCommand': [item.replace(str(ROOT), 'PROJECT') for item in command]}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'built': str(out.relative_to(ROOT) / 'playback-harness'),
        'engineSHA256': identity['engineSHA256'], 'executableSHA256': manifest['executableSHA256'],
        'replacements': len(changes), 'ran': False}))


if __name__ == '__main__':
    main()
