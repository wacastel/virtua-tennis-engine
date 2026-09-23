import Foundation

func testFrameScheduling() throws {
    var policy = VTContinuationPolicy()
    let first = policy.request(balance: -0.01, updateSeconds: 0.02, eligible: true)!
    try check(policy.request(balance: 1, updateSeconds: 0.01, eligible: true) == nil, "Only one pending continuation")
    policy.invalidate()
    let replacement = policy.request(balance: 0, updateSeconds: 0.01, eligible: true)!
    try check(!policy.take(first) && policy.pending == replacement, "External update invalidates old token without removing newer work")
    try check(policy.take(replacement) && !policy.take(replacement), "Continuation ticket executes once")
    for eligible in [false, false, false] {
        policy.invalidate()
        try check(policy.request(balance: 0.2, updateSeconds: 0.01, eligible: eligible) == nil, "Paused, failed or stopped work cannot schedule")
    }
    for pair in [(Double.nan,0.01),(0,Double.nan),(0,-0.001),(0,0.25),(0,1.0)] {
        try check(policy.request(balance: pair.0, updateSeconds: pair.1, eligible: true) == nil, "Invalid or discontinuous wall duration cannot schedule")
    }
    try check(policy.request(balance: -0.05, updateSeconds: 0.02, eligible: true) == nil, "Ahead-of-wall neutral presentation waits normally")
    var clock = VTFrameClock()
    _ = clock.update(0); _ = clock.update(0.1)
    let before = clock.balance
    _ = clock.update(0.1)
    try check(clock.balance == before, "Repeated same timestamp never double-credits wall time")
    try clock.consume(0.01672)
    _ = clock.update(0.12)
    let atEnd = clock.balance
    _ = clock.update(0.125)
    try check(abs(clock.balance-atEnd-0.005)<1e-12, "End accounting makes the next update credit idle time only")
    var late = VTFrameClock(); _ = late.update(0); _ = late.update(0.2)
    try late.consume(0.01672); _ = late.update(0.28)
    try check(late.balance>0.25 && late.discardExcess(), "Fully accounted CPU time exposes real250ms backlog immediately")
    let stale = policy.request(balance: clock.balance, updateSeconds: 0.01, eligible: true)!
    policy.invalidate(); clock.reset(); _ = clock.update(100)
    try check(clock.balance == 0 && !policy.take(stale), "Reset clears old time and queued work together")
    _ = clock.update(100.3)
    try check(clock.discardedGaps == 1 && clock.balance == 0, "Original250ms discontinuity remains enforced")

    struct Trial { var ratio: Double; var gaps: Int; var inputs: [UInt32]; var maximumSteps: Int }
    func trial(continuations: Bool, cost: Double, variable: Bool = false) throws -> Trial {
        var c = VTFrameClock(); _ = c.update(0)
        var now=1.0/60, frame=0, stepsMax=0, injected=false
        var inputs=[UInt32](); var p=VTContinuationPolicy()
        while now < 8 {
            p.invalidate()
            let started=now
            let valid=c.update(started)
            var count=0
            if valid {
                while c.wantsStep && count<1 {
                    // Inputs are indexed by actual native steps, never callbacks.
                    inputs.append(UInt32((frame*13)%256))
                    var duration=cost
                    if !injected && frame>=60 { duration+=0.14; injected=true }
                    now+=duration
                    let emulated=variable && frame%7==0 ? 0.0501 : 0.01672
                    try c.consume(emulated); frame+=1; count+=1
                }
            }
            if valid { _ = c.update(now) }
            let discarded=c.discardExcess(); stepsMax=max(stepsMax,count)
            let tick=(floor(now*60+1e-8)+1)/60
            if continuations, let ticket=p.request(balance:c.balance,updateSeconds:0,eligible:valid && !discarded) {
                guard p.take(ticket) else { throw VirtuaTennisError.message("Simulation ticket could not be claimed") }
                now+=0.0001
            } else { now=tick }
        }
        let emulated = inputs.indices.reduce(0.0) { $0 + (variable && $1%7==0 ? 0.0501 : 0.01672) }
        return Trial(ratio:emulated/now,gaps:c.discardedGaps,inputs:inputs,maximumSteps:stepsMax)
    }
    let stalled = try trial(continuations:false,cost:0.01670)
    let recovered = try trial(continuations:true,cost:0.01670)
    try check(recovered.ratio>stalled.ratio, "Fair one-step catch-up reduces timer idle without changing guard")
    try check(Array(recovered.inputs.prefix(stalled.inputs.count))==stalled.inputs, "Every native input in the common prefix is conserved")
    try check(recovered.maximumSteps==1, "One-step chunk remains below original eight-step upper bound")
    let deficit = try trial(continuations:true,cost:0.0215)
    try check(deficit.gaps>0 && deficit.ratio<0.9, "Sustained CPU deficit still fails original clock guard")
    let variable = try trial(continuations:true,cost:0.009,variable:true)
    try check(variable.gaps==0 && variable.ratio<1.01, "Variable50/17ms boundaries do not accelerate the game")
    // Exercise the exact queue used by the derived Scene, never an engine.
    let queue=VTFrameContinuation()
    var cancelled=0, live=0
    try check(queue.schedule(balance:0.01,eligible:true) { cancelled+=1 }, "Future timer can be queued")
    try check(!queue.schedule(balance:0.01,eligible:true) { cancelled+=1 }, "Queue coalesces pending timer")
    queue.cancel()
    try check(queue.schedule(balance:0.01,eligible:true) { live+=1 }, "External update replaces cancelled timer")
    RunLoop.main.run(until:Date().addingTimeInterval(0.01))
    try check(cancelled==0 && live==1, "Cancelled timer cannot consume work or cancel the replacement")
    try check(!queue.schedule(balance:0.1,eligible:false) { live+=1 }, "Paused or stopped queue schedules nothing")
    try check(!queue.schedule(balance:0,eligible:true) { live+=1 }, "No real debt means no extra callback")
    var chunks=0, timerDuringChunks=0, lastDisplay=0, maxChunksBetweenDisplays=0
    let displayTimer=Timer(timeInterval:1.0/60,repeats:true) { _ in
        timerDuringChunks+=1
        maxChunksBetweenDisplays=max(maxChunksBetweenDisplays,chunks-lastDisplay)
        lastDisplay=chunks
    }
    RunLoop.main.add(displayTimer,forMode:.common)
    func enqueueChunk() {
        _ = queue.schedule(balance:0.01,eligible:true) {
            chunks+=1
            Thread.sleep(forTimeInterval:0.02) // Indivisible synthetic step, no CPU load.
            if chunks<12 { enqueueChunk() }
        }
    }
    enqueueChunk()
    let deadline=Date().addingTimeInterval(1)
    while chunks<12 && Date()<deadline { RunLoop.main.run(until:Date().addingTimeInterval(0.001)) }
    displayTimer.invalidate();queue.cancel()
    try check(chunks==12 && timerDuringChunks>0 && maxChunksBetweenDisplays<=2,
              "Overdue display timers run between bounded20ms native-step simulations")
}
