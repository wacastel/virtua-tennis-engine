import Foundation

/// The NAOMI renderer may yield one presentation after several vblanks. Charge
/// the actual SH-4 time, retaining a small negative balance after a long step.
struct VTFrameClock {
    private var previous: TimeInterval?
    private(set) var balance: TimeInterval = 0
    private(set) var discardedGaps = 0
    var wantsStep: Bool { balance > 0 }
    mutating func reset() { previous = nil; balance = 0 }
    /// Returns false for startup or a discontinuity; callers flush on a gap.
    mutating func update(_ time: TimeInterval) -> Bool {
        defer { previous = time }
        guard time.isFinite, let old = previous else { return false }
        let elapsed = time - old
        guard elapsed >= 0, elapsed < 0.25 else {
            balance = 0; discardedGaps += 1; return false
        }
        balance += elapsed; return true
    }
    mutating func consume(_ seconds: TimeInterval) throws {
        guard seconds.isFinite, seconds > 0, seconds < 1 else {
            throw VirtuaTennisError.message("Invalid native clock interval.")
        }
        balance -= seconds
    }
    mutating func discardExcess() -> Bool {
        guard balance > 0.25 else { return false }
        balance = 0; discardedGaps += 1; return true
    }
}
