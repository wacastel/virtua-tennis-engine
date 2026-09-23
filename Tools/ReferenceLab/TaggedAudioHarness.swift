// SPDX-License-Identifier: GPL-2.0-only
// Developer-only synthetic generated PCM. Never links a game engine or a microphone.
// Compile beside a byte-identical copy of Sources/Mac/AudioOutput.swift.
import Foundation
import AVFoundation
import CryptoKit

private func now() -> Double { ProcessInfo.processInfo.systemUptime }
private func hash(_ bytes: Data) -> String { SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined() }

private struct TapBlock {
    var first: Int = 0
    var count: Int = 0
    var wall: Double = 0
    var hostSeconds: Double = 0
    var sampleTime: Int64 = 0
    var rate: Double = 0
    var hostValid = false
    var sampleValid = false
}

private final class Capture {
    let capacity = 44100 * 12
    private let samples: UnsafeMutablePointer<Float>
    private let blocks: UnsafeMutablePointer<TapBlock>
    private let blockCapacity = 8192
    private let lock = NSLock()
    private var accepting = true
    private var used = 0
    private var blockCount = 0
    private var overflow = false
    init() {
        samples = .allocate(capacity: capacity * 2)
        samples.initialize(repeating: 0, count: capacity * 2)
        blocks = .allocate(capacity: blockCapacity)
        blocks.initialize(repeating: TapBlock(), count: blockCapacity)
    }
    deinit { samples.deallocate(); blocks.deallocate() }
    func receive(_ buffer: AVAudioPCMBuffer, _ time: AVAudioTime) {
        lock.lock(); defer { lock.unlock() }
        guard accepting else { return }
        let count = Int(buffer.frameLength)
        guard used + count <= capacity, blockCount < blockCapacity,
              let channel = buffer.floatChannelData, buffer.format.channelCount == 2 else {
            overflow = true; return
        }
        blocks[blockCount] = TapBlock(first: used, count: count, wall: now(),
            hostSeconds: time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime) : 0,
            sampleTime: time.sampleTime, rate: buffer.format.sampleRate,
            hostValid: time.isHostTimeValid, sampleValid: time.isSampleTimeValid)
        for i in 0..<count { samples[(used+i)*2] = channel[0][i]; samples[(used+i)*2+1] = channel[1][i] }
        used += count; blockCount += 1
    }
    func finish(_ output: URL, sent: Int) throws -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        accepting = false
        let data = Data(bytes: samples, count: used*2*MemoryLayout<Float>.stride)
        try data.write(to: output.appendingPathComponent("player-tap-f32le.pcm"), options: .atomic)
        var metadata: [[String: Any]] = []
        for i in 0..<blockCount {
            let b = blocks[i]
            metadata.append(["firstCapturedFrame":b.first,"frameCount":b.count,"callbackWallSeconds":b.wall,
                "hostSeconds":b.hostSeconds,"hostTimeValid":b.hostValid,"sampleTime":b.sampleTime,
                "sampleTimeValid":b.sampleValid,"sampleRate":b.rate])
        }
        let blockData = try JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys,.prettyPrinted])
        try blockData.write(to: output.appendingPathComponent("tap-blocks.json"), options:.atomic)
        var zeroFrames = 0, invalidFrames = 0, duplicateFrames = 0, reverseFrames = 0
        var missingInside = 0, firstTag: Int?, lastTag: Int?
        var transitions: [[String: Any]] = []
        var zeroStart: Int?
        func endZero(_ end: Int) {
            if let a = zeroStart { transitions.append(["kind":"zeroRun","firstCapturedFrame":a,"frameCount":end-a]); zeroStart = nil }
        }
        for i in 0..<used {
            let l=samples[i*2], r=samples[i*2+1]
            if l == 0 && r == 0 { zeroFrames += 1; if zeroStart == nil { zeroStart=i }; continue }
            endZero(i)
            let ld=Double(l)*32768, rd=Double(r)*32768
            guard ld.isFinite, rd.isFinite, ld >= 1, ld <= 16384, rd >= 1, rd <= 16384,
                  abs(ld-ld.rounded()) < 0.01, abs(rd-rd.rounded()) < 0.01 else {
                invalidFrames += 1; continue
            }
            let tag=(Int(ld.rounded())-1) | ((Int(rd.rounded())-1)<<14)
            guard tag < sent else { invalidFrames += 1; continue }
            if firstTag == nil { firstTag=tag }
            if let prev=lastTag, tag != prev+1 {
                if tag == prev { duplicateFrames += 1 }
                else if tag < prev { reverseFrames += 1 }
                else { missingInside += tag-prev-1 }
                if transitions.count < 5000 { transitions.append(["kind":"tagDiscontinuity","capturedFrame":i,"previousTag":prev,"nextTag":tag,"delta":tag-prev]) }
            }
            lastTag=tag
        }
        endZero(used)
        let transitionData = try JSONSerialization.data(withJSONObject: transitions,options:[.prettyPrinted,.sortedKeys])
        try transitionData.write(to:output.appendingPathComponent("tap-transitions.json"),options:.atomic)
        return ["capturedStereoFrames":used,"tapBlocks":blockCount,"overflow":overflow,
            "tapPCM_SHA256":hash(data),"tapMetadataSHA256":hash(blockData),"zeroFrames":zeroFrames,
            "invalidTagFrames":invalidFrames,"duplicateTagFrames":duplicateFrames,"reverseTagFrames":reverseFrames,
            "missingFramesBetweenValidTags":missingInside,"firstTag":firstTag as Any? ?? NSNull(),"lastTag":lastTag as Any? ?? NSNull(),
            "allSubmittedTagsObservedExactlyOnceInOrder":firstTag == 0 && lastTag == sent-1 && missingInside == 0 && duplicateFrames == 0 && reverseFrames == 0 && invalidFrames == 0,
            "scope":"Tap observes generated PCM at the player node before the muted mixer. It is not a microphone, speaker capture or hardware xrun detector. Missing callbacks while stopped need host timestamps, not concatenated PCM duration, to interpret."]
    }
}

@main
private enum Main {
    static func main() throws {
        let args=CommandLine.arguments
        let recipeMode = args.count == 4 && args[1] == "recipe"
        guard recipeMode || (args.count == 3 && ["steady","starvation","backlog"].contains(args[1])) else {
            throw NSError(domain:"TaggedAudioBaseline",code:1,userInfo:[NSLocalizedDescriptionKey:"usage: tagged-audio-harness steady|starvation|backlog OUTPUT, or tagged-audio-harness recipe RECIPE.json OUTPUT"])
        }
        var recipe: [[String:Any]] = [], recipeSHA = "", recipeDuration:Double = 0
        if recipeMode {
            let data=try Data(contentsOf:URL(fileURLWithPath:args[2]))
            let value=try JSONSerialization.jsonObject(with:data) as! [String:Any]
            recipe=value["events"] as! [[String:Any]];recipeDuration=value["durationSeconds"] as! Double;recipeSHA=hash(data)
            precondition(recipeDuration > 0 && recipeDuration <= 15)
            var last:Double = -1
            for e in recipe {
                let time=e["arrivalSeconds"] as! Double
                precondition(time >= last && time < recipeDuration-0.4);last=time
                precondition(e["kind"] as! String == "flush" || ((e["batch"] as! Int)>0 && (e["batch"] as! Int)<=11025))
            }
        }
        let scenario=recipeMode ? "recipe" : args[1], output=URL(fileURLWithPath:args[recipeMode ? 3:2],isDirectory:true)
        guard !FileManager.default.fileExists(atPath:output.appendingPathComponent("report.json").path) else {
            throw NSError(domain:"TaggedAudioBaseline",code:2,userInfo:[NSLocalizedDescriptionKey:"Refusing to overwrite an existing report"])
        }
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let capture=Capture(), audio=VTAudioOutput(muted:true,sampleRate:44100)
        audio.installRenderTap(bufferSize:512) { [capture] buffer,time in capture.receive(buffer,time) }
        let start=now(), duration=recipeMode ? recipeDuration:4.0, period=738.0/44100
        var sent=0, sequence=0, deadline=start, triggered=false, waiting=false
        var inputData=Data(), events:[[String:Any]]=[]
        events.reserveCapacity(500)
        func record(_ kind:String, _ fields:[String:Any]=[:]) {
            var row=fields;row["kind"]=kind;row["wallSeconds"]=now()-start;row["submittedTotalFrames"]=sent
            events.append(row)
        }
        func send(_ count:Int, beforeSnapshot: [String:Any]? = nil) {
            let before=beforeSnapshot ?? audio.diagnostics(), arrival=now()
            var pcm=[Int16](repeating:0,count:count*2)
            for i in 0..<count {
                let tag=sent+i
                pcm[i*2]=Int16((tag&0x3fff)+1)
                pcm[i*2+1]=Int16(((tag>>14)&0x3fff)+1)
            }
            pcm.withUnsafeBytes { inputData.append(contentsOf:$0) }
            audio.present(pcm);sent+=count;sequence+=1
            record("enqueue",["batch":count,"sequence":sequence,"arrivalSeconds":arrival-start,"presentReturnSeconds":now()-start,"before":before,"after":audio.diagnostics()])
        }
        record("measurementStart",["diagnostics":audio.diagnostics()])
        var recipeIndex=0
        while now()-start < duration {
            let current=now(), elapsed=current-start
            if recipeMode {
                if recipeIndex < recipe.count, elapsed >= (recipe[recipeIndex]["arrivalSeconds"] as! Double) {
                    let e=recipe[recipeIndex]
                    if e["kind"] as! String == "flush" { audio.flush();record("recipeHostFlush",["scheduledArrivalSeconds":e["arrivalSeconds"]!]) }
                    else { send(e["batch"] as! Int) }
                    recipeIndex+=1
                } else { RunLoop.main.run(until:Date().addingTimeInterval(0.0005)) }
                continue
            }
            if elapsed >= 1, !triggered, scenario == "starvation" {
                triggered=true;waiting=true;record("withholdBegin",["diagnostics":audio.diagnostics()])
            }
            if waiting {
                let d=audio.diagnostics(), pending=(d["pendingFrames"] as? Int64) ?? Int64((d["pendingFrames"] as? Int) ?? 0)
                if pending < 0 {
                    record("withholdEnd",["sampledDeficitFrames":-pending,"diagnostics":d])
                    waiting=false;send(738);deadline=now()+period
                }
            } else if elapsed >= 1, !triggered, scenario == "backlog" {
                triggered=true;record("catchUpBegin",["diagnostics":audio.diagnostics()])
                // Deliberately accumulate near-cap queue with <=one ordinary batch per call.
                // Each present() still performs all unchanged production checks.
                for _ in 0..<30 {
                    let d=audio.diagnostics(), pending=(d["pendingFrames"] as? Int64) ?? Int64((d["pendingFrames"] as? Int) ?? 0)
                    if pending > 11025 {
                        // Consume the already-observed near-cap condition immediately;
                        // an extra expensive diagnostic query can cross a render quantum.
                        send(738,beforeSnapshot:d)
                        record("catchUpThresholdConsumed",["observedPendingFrames":pending])
                        break
                    }
                    send(min(738,max(1,Int(11025+64-pending))))
                }
                record("catchUpEnd",["diagnostics":audio.diagnostics()])
                deadline=now()+period
            } else if current >= deadline, elapsed < duration - 0.25 {
                send(738);deadline+=period
            }
            RunLoop.main.run(until:Date().addingTimeInterval(0.0005))
        }
        let final=audio.diagnostics();record("measurementEnd",["diagnostics":final])
        audio.removeRenderTap()
        let tap=try capture.finish(output,sent:sent)
        audio.flush()
        try inputData.write(to:output.appendingPathComponent("submitted-s16le.pcm"),options:.atomic)
        let eventData=try JSONSerialization.data(withJSONObject:events,options:[.prettyPrinted,.sortedKeys])
        try eventData.write(to:output.appendingPathComponent("producer-events.json"),options:.atomic)
        let result:[String:Any]=["scenario":scenario,"wallSeconds":now()-start,"sourceRate":44100,
            "submittedStereoFrames":sent,"inputPCM_SHA256":hash(inputData),"eventsSHA256":hash(eventData),
            "finalAudio":final,"tap":tap,"syntheticTriggerObserved":scenario == "steady" || triggered,
            "recipeSHA256":recipeSHA,"recipeEventsConsumed":recipeIndex,"recipeEventsExpected":recipe.count,
            "scope":"AudioOutput policy is bound by the build manifest; generated indexed PCM only; muted mixer; no game, controller, microphone, visible window or preference changes. Tap instrumentation may affect scheduling."]
        try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]).write(to:output.appendingPathComponent("report.json"),options:.atomic)
        print(String(data:try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]),encoding:.utf8)!)
    }
}
