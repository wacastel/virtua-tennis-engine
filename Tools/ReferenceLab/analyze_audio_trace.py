#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read-only producer-event analysis; no inference of acoustic/device xruns.

Usage: analyze_audio_trace.py DIRECTORY [--output PATH]
Consumes audio-batches.jsonl and report.json. No playback or source changes.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    a = sorted(x for x in values if x is not None and math.isfinite(x))
    if not a:
        return {"count": 0}
    return {"count": len(a), "sum": sum(a), "min": a[0],
            "median": statistics.median(a), "p95": a[min(len(a)-1, math.ceil(.95*len(a))-1)],
            "p99": a[min(len(a)-1, math.ceil(.99*len(a))-1)], "max": a[-1]}


def analyze(events, report, trace_sha256):
    issues = []
    begins = [e for e in events if e["kind"] == "measurementStart"]
    ends = [e for e in events if e["kind"] == "measurementEnd"]
    if len(begins) != 1 or len(ends) != 1:
        raise ValueError("Need exactly one completed measurement interval")
    start, end = begins[0]["wallSeconds"], ends[0]["wallSeconds"]
    seq0, seq1 = begins[0]["sequence"], ends[0]["sequence"]
    if seq0 >= seq1 or not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("Measurement interval is reversed or invalid")
    scoped = [e for e in events if seq0 < e["sequence"] < seq1]
    if [e["sequence"] for e in events] != list(range(1, len(events)+1)):
        issues.append("Non-contiguous event sequences")
    if any(b["wallSeconds"] < a["wallSeconds"] for a,b in zip(events, events[1:])):
        issues.append("Event monotonic timestamp regression")
    binding = report.get("batchTrace", {})
    if binding.get("complete") is not True or binding.get("droppedEvents") != 0:
        issues.append("Recorder does not certify a complete untruncated trace")
    if binding.get("sha256") != trace_sha256:
        issues.append("Trace bytes differ from the recorder report SHA256")
    if binding.get("recordedEvents") != len(events):
        issues.append("Trace event count differs from the recorder report")
    if (binding.get("measurementStartSeconds") != start
            or binding.get("measurementEndSeconds") != end):
        issues.append("Measurement boundaries differ from the recorder report")

    by_frame = defaultdict(dict)
    for e in scoped:
        if e["kind"] in ("nativeReturn", "gameReturn", "enqueueBefore", "enqueueAfter"):
            if e["kind"] in by_frame[e["frame"]]:
                issues.append(f"Repeated {e['kind']} for frame {e['frame']}")
            by_frame[e["frame"]][e["kind"]] = e
    steps = []
    previous = None
    for frame, row in sorted(by_frame.items()):
        n, g, b, a = [row.get(k) for k in ("nativeReturn", "gameReturn", "enqueueBefore", "enqueueAfter")]
        if not n or not g:
            issues.append(f"Incomplete native/game pair at frame {frame}")
            continue
        if g["batchSampleFrames"] and (not b or not a):
            issues.append(f"Incomplete nonempty enqueue pair at frame {frame}")
        if g["batchSampleFrames"] < 0 or not math.isfinite(g["sampleRate"]) or g["sampleRate"] <= 0:
            issues.append(f"Invalid generated sample count/rate at frame {frame}")
        if n["sequence"] >= g["sequence"]:
            issues.append(f"Native/game event order is invalid at frame {frame}")
        if b and a:
            if not n["sequence"] < g["sequence"] < b["sequence"] < a["sequence"]:
                issues.append(f"Native/game/enqueue event order is invalid at frame {frame}")
            if not (g["batchSampleFrames"] == b["batchSampleFrames"] == a["batchSampleFrames"]):
                issues.append(f"Generated/enqueued sample counts differ at frame {frame}")
            if not (g["sampleRate"] == b["sampleRate"] == a["sampleRate"]):
                issues.append(f"Generated/enqueued sample rates differ at frame {frame}")
            if (b["enqueueArrivalSeconds"] != a["enqueueArrivalSeconds"]
                    or not n["nativeStartSeconds"] <= n["wallSeconds"] <= g["wallSeconds"]
                    <= b["enqueueArrivalSeconds"] <= b["wallSeconds"] <= a["wallSeconds"]):
                issues.append(f"Native/game/enqueue timestamps are inconsistent at frame {frame}")
        if not g["batchSampleFrames"] and (b or a):
            issues.append(f"Empty generated batch unexpectedly enqueued at frame {frame}")
        dt = g["emulatedSeconds"] - n["emulatedSeconds"]
        native = n["wallSeconds"] - n["nativeStartSeconds"]
        x = {"frame": frame, "wallSeconds": n["nativeStartSeconds"]-start,
             "nativeSeconds": native, "emulatedSeconds": dt,
             "nativeMinusEmulatedSeconds": native-dt,
             "nativeToGameReturnSeconds": g["wallSeconds"]-n["wallSeconds"],
             "batchSampleFrames": g["batchSampleFrames"], "sampleRate": g["sampleRate"]}
        if b and a:
            x.update({"enqueueWallSeconds": b["wallSeconds"]-start,
                      "enqueueSeconds": a["wallSeconds"]-b["enqueueArrivalSeconds"],
                      "gameToEnqueueSeconds": b["enqueueArrivalSeconds"]-g["wallSeconds"],
                      "pendingBeforeFrames": b.get("pendingSampleFrames"),
                      "pendingAfterFrames": a.get("pendingSampleFrames")})
            if previous:
                x["producerArrivalGapSeconds"] = b["enqueueArrivalSeconds"]-previous["enqueueArrivalSeconds"]
            previous = b
        steps.append(x)
    step_by_frame = {x["frame"]: x for x in steps}

    # Queue epochs are strictly bounded by stop/flush, never compared across reset clocks.
    epoch = 0
    epochs = defaultdict(list)
    flushes = []
    starts = []
    last_before = None
    last_queue_sample = None
    schedule_checks = 0
    measured_schedule_checks = 0
    current_batch = None
    batch_flushed = False
    for e in events:
        if e["kind"] == "enqueueBefore":
            last_before = e
            current_batch = e
            batch_flushed = False
            if e.get("renderedSampleFrames") is not None:
                identity = e["scheduledSampleFrames"]-e["renderedSampleFrames"]
                if identity != e["pendingSampleFrames"]:
                    issues.append(f"Broken queue identity at sequence {e['sequence']}")
                epochs[epoch].append(e)
                last_queue_sample = e
        elif e["kind"] == "enqueueAfter":
            if current_batch and current_batch["frame"] == e["frame"]:
                expected = (0 if batch_flushed else current_batch["scheduledSampleFrames"]) + current_batch["batchSampleFrames"]
                schedule_checks += 1
                if seq0 < e["sequence"] < seq1:
                    measured_schedule_checks += 1
                if e["scheduledSampleFrames"] != expected:
                    issues.append(f"Submitted sample discontinuity at sequence {e['sequence']}")
            elif seq0 < e["sequence"] < seq1:
                issues.append(f"Enqueue completion lacks its preceding batch at sequence {e['sequence']}")
            current_batch = None
            if e.get("renderedSampleFrames") is not None:
                if e["pendingSampleFrames"] != e["scheduledSampleFrames"]-e["renderedSampleFrames"]:
                    issues.append(f"Broken after-enqueue queue identity at sequence {e['sequence']}")
                last_queue_sample = e
        elif e["kind"] == "flush":
            epoch += 1
            batch_flushed = True
            if not seq0 < e["sequence"] < seq1:
                last_queue_sample = None
                continue
            same_batch = last_before is not None and last_before["frame"] == e["frame"]
            # Underrun/backlog are inside present(); host flush can happen afterward.
            exact = same_batch and e["reason"] in ("underrun", "backlog")
            rate = last_before.get("sampleRate", 44100) if last_before else 44100
            pending = last_before.get("pendingSampleFrames") if exact else None
            f = {"sequence": e["sequence"], "frame": e["frame"],
                 "wallSeconds": e["wallSeconds"]-start, "reason": e["reason"],
                 "playerWasStarted": e["playerStarted"],
                 "submittedFramesBeforeStop": e["scheduledSampleFrames"],
                 "exactSampledPendingFrames": pending,
                 "exactSampledPendingSeconds": pending/rate if pending is not None else None,
                 "queueSampleAgeSeconds": e["wallSeconds"]-last_before["wallSeconds"] if exact else None,
                 "lastQueueObservation": ({"kind": last_queue_sample["kind"], "frame": last_queue_sample["frame"],
                     "pendingSampleFrames": last_queue_sample["pendingSampleFrames"],
                     "ageSeconds": e["wallSeconds"]-last_queue_sample["wallSeconds"],
                     "scope": "Earlier sampled queue, not exact queue remaining at stop"} if last_queue_sample else None),
                 "hostReasonDetail": "unknown: clock debt/discontinuity or lifecycle" if e["reason"] == "host" else None,
                 "step": step_by_frame.get(e["frame"])}
            if pending is not None:
                f["sampledDeficitSeconds"] = max(0, -pending)/rate
                f["sampledBacklogThresholdExcessSeconds"] = max(0, pending-int(rate*.25))/rate
            flushes.append(f)
            last_queue_sample = None
        elif e["kind"] == "playerStart" and seq0 < e["sequence"] < seq1:
            starts.append(e)

    nonempty_batches = sum(x["batchSampleFrames"] > 0 for x in steps)
    if measured_schedule_checks != nonempty_batches:
        issues.append("Not every nonempty measured generated batch has exactly one schedule check")

    for f in flushes:
        next_start = next((e for e in starts if e["sequence"] > f["sequence"]), None)
        next_flush = next((g for g in flushes if g["sequence"] > f["sequence"]), None)
        f["nextPlayCallDelaySeconds"] = next_start["wallSeconds"]-start-f["wallSeconds"] if next_start else None
        f["nextPlayCallFrame"] = next_start["frame"] if next_start else None
        f["nextPlayCallSubmittedFrames"] = next_start["scheduledSampleFrames"] if next_start else None
        f["anotherFlushBeforeNextPlay"] = bool(next_flush and (not next_start or next_flush["sequence"] < next_start["sequence"]))
        f["nextRecoveryDelaySeconds"] = next_flush["wallSeconds"]-f["wallSeconds"] if next_flush else None

    queue_epochs = []
    for number, rows in sorted(epochs.items()):
        rows = [e for e in rows if seq0 < e["sequence"] < seq1]
        if len(rows) < 2:
            continue
        first, last = rows[0], rows[-1]
        rate = first["sampleRate"]
        duration = last["wallSeconds"]-first["wallSeconds"]
        xs = [e["wallSeconds"]-first["wallSeconds"] for e in rows]
        ys = [(e["renderedSampleFrames"]-first["renderedSampleFrames"])/rate for e in rows]
        meanx, meany = statistics.mean(xs), statistics.mean(ys)
        denom = sum((x-meanx)**2 for x in xs)
        slope = sum((x-meanx)*(y-meany) for x,y in zip(xs,ys))/denom if denom else None
        jumps = Counter(b["renderedSampleFrames"]-a["renderedSampleFrames"] for a,b in zip(rows,rows[1:]))
        queue_epochs.append({"epoch": number, "firstFrame": first["frame"], "lastFrame": last["frame"],
            "sampleCount": len(rows), "wallSpanSeconds": duration,
            "sampledQueueGrowthSeconds": (last["pendingSampleFrames"]-first["pendingSampleFrames"])/rate,
            "submittedSpanSeconds": (last["scheduledSampleFrames"]-first["scheduledSampleFrames"])/rate,
            "renderedSpanSeconds": (last["renderedSampleFrames"]-first["renderedSampleFrames"])/rate,
            "sampledRenderSlope": slope, "commonRenderSampleDeltas": jumps.most_common(8),
            "scope": "Sampled player timeline slope; not independent hardware-clock or acoustic measurement"})

    # Group nearby recoveries for causal inspection without equating counts with audible events.
    cascades = []
    for f in flushes:
        if not cascades or f["wallSeconds"]-cascades[-1][-1]["wallSeconds"] > 2:
            cascades.append([])
        cascades[-1].append(f)
    grouped = []
    for group in cascades:
        t0, t1 = group[0]["wallSeconds"], group[-1]["wallSeconds"]
        local = [x for x in steps if t0-.5 <= x["wallSeconds"] <= t1+.5]
        grouped.append({"firstWallSeconds": t0, "lastWallSeconds": t1,
            "firstFrame": group[0]["frame"], "lastFrame": group[-1]["frame"],
            "reasons": dict(Counter(f["reason"] for f in group)),
            "longNativeStepsInHalfSecondContext": sum(x["nativeMinusEmulatedSeconds"] > 0 for x in local),
            "nativeSecondsInContext": sum(x["nativeSeconds"] for x in local),
            "emulatedSecondsInContext": sum(x["emulatedSeconds"] for x in local),
            "recoveries": group})

    # Recent slow-step accumulation; no reset/debt algorithm is inferred from this sum.
    runs, run = [], []
    for x in steps:
        if x["nativeMinusEmulatedSeconds"] > 0:
            run.append(x)
        elif run:
            runs.append(run); run = []
    if run:
        runs.append(run)
    slow = [{"firstFrame": r[0]["frame"], "lastFrame": r[-1]["frame"],
             "wallSeconds": r[0]["wallSeconds"], "steps": len(r),
             "nativeSeconds": sum(x["nativeSeconds"] for x in r),
             "emulatedSeconds": sum(x["emulatedSeconds"] for x in r),
             "nativeExcessSeconds": sum(x["nativeMinusEmulatedSeconds"] for x in r)} for r in runs]
    slow.sort(key=lambda x: x["nativeExcessSeconds"], reverse=True)

    # Compare report increments to the exact interval; initialization/shutdown are separate.
    measured_counts = Counter(f["reason"] for f in flushes)
    pre_counts = Counter(e["reason"] for e in events if e["kind"] == "flush" and e["sequence"] < seq0)
    post_counts = Counter(e["reason"] for e in events if e["kind"] == "flush" and e["sequence"] > seq1)
    recorded_report = report.get("flushCounts", {})
    if recorded_report and Counter(recorded_report) != measured_counts+pre_counts:
        issues.append("Report flush totals disagree with premeasurement + measured events")
    produced = sum(x["batchSampleFrames"] for x in steps)
    rates = sorted(set(x["sampleRate"] for x in steps))
    rate = rates[0] if len(rates) == 1 else None
    deficits = [f for f in flushes if f["reason"] == "underrun"]
    reprimes = [f["nextPlayCallDelaySeconds"] for f in deficits if f["nextPlayCallDelaySeconds"] is not None]
    quantum_candidate = 512/48000
    deltas = [b["renderedSampleFrames"]-a["renderedSampleFrames"]
              for rows in epochs.values() for a,b in zip(rows,rows[1:])
              if b["sequence"] < seq1 and a["sequence"] > seq0]
    matched = sum(abs(d-round(d/(quantum_candidate*rate))*quantum_candidate*rate) <= 1.01 for d in deltas) if rate else None
    return {"schemaVersion": 1, "measurementSeconds": end-start, "integrityIssues": issues,
        "measurementEventCount": len(scoped), "eventCounts": dict(Counter(e["kind"] for e in events)),
        "flushCounts": {"beforeMeasurement": dict(pre_counts), "duringMeasurement": dict(measured_counts),
                        "afterMeasurement": dict(post_counts), "report": recorded_report},
        "nativeSteps": len(steps), "playerPlayCalls": len(starts),
        "sampleAccounting": {"producedStereoSampleFrames": produced, "sourceSampleRates": rates,
             "producedAudioSeconds": produced/rate if rate else None,
             "emulatedSeconds": sum(x["emulatedSeconds"] for x in steps),
             "submittedBatchIdentityChecks": schedule_checks,
             "measuredNonemptyBatches": nonempty_batches,
             "measuredSubmittedBatchIdentityChecks": measured_schedule_checks,
             "sumSampledUnderrunDeficitFrames": sum(-f["exactSampledPendingFrames"] for f in deficits),
             "sumSampledUnderrunDeficitSeconds": sum(f["sampledDeficitSeconds"] for f in deficits),
             "sumUnderrunStopToPlaySeconds": sum(reprimes),
             "underrunStopToPlaySeconds": stats(reprimes),
             "scope": ("Measured generated/enqueued counts, rates and scheduled-frontier increments agree for one submission per nonempty batch. " if not issues else "Integrity checks failed; complete sample submission is not established. ")
                 + "Stop removes remaining scheduled audio; actual rendered/dropped PCM cannot be recovered from timeline metadata alone. Summed deficits and stop-to-play durations are observations, not total acoustic silence."},
        "renderSnapshotQuantization": {"hypothesisSeconds": quantum_candidate,
             "hypothesis": "512 output frames at48000Hz (470.4 source frames at44100Hz)",
             "adjacentSampleDeltas": len(deltas), "withinOneSourceFrameOfHypothesizedQuantumMultiple": matched,
             "scope": "Observed compatibility only; this trace has no render callback size or node hostTime."},
        "timing": {key: stats(x.get(key) for x in steps) for key in
             ("nativeSeconds", "emulatedSeconds", "nativeMinusEmulatedSeconds", "nativeToGameReturnSeconds",
              "gameToEnqueueSeconds", "enqueueSeconds", "producerArrivalGapSeconds")},
        "nativeLongerThanEmulatedSteps": sum(x["nativeMinusEmulatedSeconds"] > 0 for x in steps),
        "topSlowRuns": slow[:20], "topNativeSteps": sorted(steps,key=lambda x:x["nativeSeconds"],reverse=True)[:20],
        "recoveryCascades": grouped, "queueEpochs": queue_epochs,
        "interpretationLimits": [
            "Negative pending is sampled player timeline beyond submitted audio, not an independent Core Audio xrun measurement.",
            "Backlog flush stops and unschedules queued PCM; its count is not starvation.",
            "Flush-to-play delay measures producer re-prime time, not acoustic silence; downstream latency/render quantum are unobserved.",
            "Host flush combines clock debt/discontinuity and lifecycle; this event stream cannot distinguish them.",
            "Player render timestamps are sampled and quantized; no extrapolation to device-now or threshold relaxation is performed.",
            "Grouped recovery counts are causal-inspection windows, not a count of audible hitches.",
            "Tracing adds timestamp/append work. Reported native excess excludes frame upload and main-loop delay; it is not scene clock debt."]}


def self_test():
    events = []
    def add(kind, wall, frame=0, **kw):
        events.append(dict(kind=kind, wallSeconds=wall, frame=frame, sequence=len(events)+1, **kw))
    add("flush", 0, reason="host", playerStarted=False, scheduledSampleFrames=0)
    add("measurementStart", .01)
    for frame, wall, before, rendered, flush in [(1,.1,0,None,None),(2,.2,2210,2254,"underrun"),(3,.3,2210,2254,"underrun"),(4,.4,15000,3975,None),(5,.5,15000,3955,"backlog")]:
        add("nativeReturn",wall,frame,nativeStartSeconds=wall-.06,nativeStatus=1,emulatedSeconds=(frame-1)*.05)
        add("gameReturn",wall+.001,frame,batchSampleFrames=2210,sampleRate=44100,emulatedSeconds=frame*.05)
        kw=dict(enqueueArrivalSeconds=wall+.001,batchSampleFrames=2210,sampleRate=44100,scheduledSampleFrames=before,playerStarted=rendered is not None)
        if rendered is not None: kw.update(renderedSampleFrames=rendered,pendingSampleFrames=before-rendered)
        add("enqueueBefore",wall+.002,frame,**kw)
        if flush: add("flush",wall+.003,frame,reason=flush,playerStarted=True,scheduledSampleFrames=before)
        if flush or frame == 1: add("playerStart",wall+.005,frame,playerStarted=True,scheduledSampleFrames=2210)
        add("enqueueAfter",wall+.006,frame,enqueueArrivalSeconds=wall+.001,batchSampleFrames=2210,sampleRate=44100,scheduledSampleFrames=(0 if flush else before)+2210,playerStarted=True,pendingSampleFrames=2210)
    add("flush",.55,5,reason="host",playerStarted=True,scheduledSampleFrames=2210)
    add("measurementEnd",.6,5)
    add("flush",.61,5,reason="host",playerStarted=True,scheduledSampleFrames=2210)
    def bound_report(rows):
        raw = "".join(json.dumps(e, sort_keys=True)+"\n" for e in rows).encode()
        digest = hashlib.sha256(raw).hexdigest()
        begin = next(e for e in rows if e["kind"] == "measurementStart")
        end = next(e for e in rows if e["kind"] == "measurementEnd")
        return {"flushCounts":{"host":2,"underrun":2,"backlog":1},
                "batchTrace":{"complete":True,"droppedEvents":0,"sha256":digest,
                    "recordedEvents":len(rows),"measurementStartSeconds":begin["wallSeconds"],
                    "measurementEndSeconds":end["wallSeconds"]}}, digest

    report, digest = bound_report(events)
    out = analyze(events,report,digest)
    assert not out["integrityIssues"], out["integrityIssues"]
    assert out["nativeSteps"] == 5 and out["nativeLongerThanEmulatedSteps"] == 5
    assert out["flushCounts"]["duringMeasurement"] == {"underrun":2,"backlog":1,"host":1}
    flat=[f for c in out["recoveryCascades"] for f in c["recoveries"]]
    assert flat[0]["exactSampledPendingFrames"] == -44
    assert abs(flat[0]["nextPlayCallDelaySeconds"]-.002)<1e-9
    assert flat[2]["exactSampledPendingFrames"] == 11045
    assert abs(flat[2]["sampledBacklogThresholdExcessSeconds"]-20/44100)<1e-9
    assert flat[3]["exactSampledPendingFrames"] is None
    assert flat[3]["nextPlayCallDelaySeconds"] is None
    assert out["sampleAccounting"]["measuredNonemptyBatches"] == 5
    assert out["sampleAccounting"]["measuredSubmittedBatchIdentityChecks"] == 5
    bad=[dict(e) for e in events]; bad[4]["sequence"] += 10
    bad_report, bad_digest = bound_report(bad)
    assert analyze(bad,bad_report,bad_digest)["integrityIssues"]

    # Rebind each altered trace so these cases test internal accounting, not
    # merely detection by the outer file hash.
    for kind in ("gameReturn", "enqueueBefore", "enqueueAfter"):
        for key, value, expected in (("batchSampleFrames",2211,"sample counts differ"),
                                     ("sampleRate",48000,"sample rates differ")):
            bad=[dict(e) for e in events]
            next(e for e in bad if e["kind"] == kind)[key] = value
            bad_report, bad_digest = bound_report(bad)
            rejected=analyze(bad,bad_report,bad_digest)
            assert any(expected in issue for issue in rejected["integrityIssues"]), rejected
            assert "not established" in rejected["sampleAccounting"]["scope"]
    for left, right in (("nativeReturn","gameReturn"), ("enqueueBefore","enqueueAfter")):
        bad=[dict(e) for e in events]
        i=next(i for i,e in enumerate(bad) if e["kind"] == left)
        j=next(i for i,e in enumerate(bad) if e["kind"] == right)
        bad[i],bad[j]=bad[j],bad[i]
        bad[i]["wallSeconds"],bad[j]["wallSeconds"]=bad[j]["wallSeconds"],bad[i]["wallSeconds"]
        for i,e in enumerate(bad): e["sequence"]=i+1
        bad_report, bad_digest = bound_report(bad)
        rejected=analyze(bad,bad_report,bad_digest)
        assert any("event order" in issue for issue in rejected["integrityIssues"]), rejected
    for key, value in (("sha256","0"*64), ("recordedEvents",len(events)-1),
                       ("complete",False), ("droppedEvents",1),
                       ("measurementStartSeconds",.02), ("measurementEndSeconds",.7)):
        bad_report=json.loads(json.dumps(report)); bad_report["batchTrace"][key]=value
        assert analyze(events,bad_report,digest)["integrityIssues"], key
    bad_report=json.loads(json.dumps(report)); del bad_report["batchTrace"]["complete"]
    assert analyze(events,bad_report,digest)["integrityIssues"]
    # A removed whole batch with clean renumbering must still fail its original
    # recorder binding, even though every remaining batch is internally valid.
    bad=[dict(e) for e in events if e["frame"] != 1]
    for i,e in enumerate(bad): e["sequence"]=i+1
    _, bad_digest = bound_report(bad)
    assert analyze(bad,report,bad_digest)["integrityIssues"]
    print("PASS: deficits/threshold, restart/lifecycle bounds, sequence and sample count/rate/order corruption, exact recorder binding and truncation")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", nargs="?", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--self-test", action="store_true")
    args=p.parse_args()
    if args.self_test:
        self_test(); return
    if args.directory is None:
        p.error("directory is required")
    trace=args.directory/"audio-batches.jsonl"; report_path=args.directory/"report.json"
    trace_bytes=trace.read_bytes()
    trace_digest=hashlib.sha256(trace_bytes).hexdigest()
    events=[json.loads(line) for line in trace_bytes.decode().splitlines() if line.strip()]
    report=json.loads(report_path.read_text())
    result=analyze(events,report,trace_digest)
    result["provenance"]={"analyzerSHA256":sha(Path(__file__)),"traceSHA256":trace_digest,"reportSHA256":sha(report_path),
                          "engineSHA256":report.get("engineSHA256"),"executableSHA256":report.get("executableSHA256")}
    out=args.output or args.directory/"event-analysis.json"
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"output":str(out),"integrityIssues":result["integrityIssues"],"steps":result["nativeSteps"],
                      "flushCounts":result["flushCounts"],"cascades":len(result["recoveryCascades"])}))
    if result["integrityIssues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
