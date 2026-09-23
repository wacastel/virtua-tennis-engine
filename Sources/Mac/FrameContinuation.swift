import Foundation

/// A ticket runs once or is invalidated by a display/lifecycle transition.
/// Queue bookkeeping never changes the game clock or advances the core.
struct VTContinuationPolicy {
    private var generation: UInt64 = 0
    private(set) var pending: UInt64?
    mutating func invalidate() { generation &+= 1; pending = nil }
    mutating func request(balance: Double, updateSeconds: Double, eligible: Bool) -> UInt64? {
        guard eligible, pending == nil, balance.isFinite, updateSeconds.isFinite,
              updateSeconds >= 0, updateSeconds < 0.25,
              balance + updateSeconds > 0 else { return nil }
        generation &+= 1; pending = generation; return generation
    }
    mutating func take(_ ticket: UInt64) -> Bool {
        guard pending == ticket else { return false }
        pending = nil; return true
    }
}

/// At most one one-shot timer, scheduled in the future.
/// A new timer cannot drain recursively in the current run-loop block phase.
final class VTFrameContinuation {
    private var policy = VTContinuationPolicy()
    private var timer: Timer?
    static let continuationDelay = 0.0001
    func cancel() {
        precondition(Thread.isMainThread)
        timer?.invalidate(); timer = nil; policy.invalidate()
    }
    @discardableResult func schedule(balance: Double, eligible: Bool, action: @escaping () -> Void) -> Bool {
        precondition(Thread.isMainThread)
        guard let ticket = policy.request(balance: balance, updateSeconds: 0, eligible: eligible) else { return false }
        let next = Timer(fire: Date(timeIntervalSinceNow: Self.continuationDelay), interval: 0, repeats: false) { [weak self] fired in
            guard let self else { return }
            if self.timer === fired { self.timer = nil }
            guard self.policy.take(ticket) else { return }
            autoreleasepool { action() }
        }
        next.tolerance = 0
        timer = next
        RunLoop.main.add(next, forMode: .common)
        return true
    }
    deinit { timer?.invalidate() }
}
