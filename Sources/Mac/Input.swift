import Foundation

enum VirtuaTennisError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}
enum VTButton {
    static let up: UInt32 = 1, down: UInt32 = 2, left: UInt32 = 4, right: UInt32 = 8
    static let shot: UInt32 = 16, lob: UInt32 = 32, start: UInt32 = 128
    static let playerMask: UInt32 = 0xbf, coin1: UInt32 = 1 << 16, coin2: UInt32 = 1 << 17, all: UInt32 = 0x3bfbf
    static func normalize(_ buttons: UInt32) -> UInt32 {
        var value = buttons
        for shift: UInt32 in [0,8] {
            for pair in [up | down, left | right] {
                let mask = pair << shift
                if value & mask == mask { value &= ~mask }
            }
        }
        return value
    }
}
struct VTInput: Codable, Equatable {
    var buttons: UInt32 = 0
    init(buttons: UInt32 = 0) {
        self.buttons = buttons
    }
    private enum CodingKeys: String, CodingKey { case buttons }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        buttons = try values.decodeIfPresent(UInt32.self, forKey: .buttons) ?? 0
    }
    func validate() throws {
        guard buttons & ~VTButton.all == 0 else {
            throw VirtuaTennisError.message("Invalid tennis input: only two direction/Shot/Lob/Start bytes and two coin flags are accepted. Reserved bit 6 in each player byte must be zero.")
        }
    }
    var diagnostic: [String: Any] {
        ["buttons": buttons]
    }
}
struct VTReplay: Decodable {
    struct Step: Decodable {
        let frames: Int
        let input: VTInput
        private enum CodingKeys: String, CodingKey { case frames }
        init(from decoder: Decoder) throws {
            frames = try decoder.container(keyedBy: CodingKeys.self).decode(Int.self, forKey: .frames)
            input = try VTInput(from: decoder)
        }
    }
    let steps: [Step]
    var frames: Int { steps.reduce(0) { $0 + $1.frames } }
    func validate() throws {
        guard !steps.isEmpty else { throw VirtuaTennisError.message("A replay must contain at least one step.") }
        var total = 0
        for step in steps {
            try step.input.validate()
            guard (1...1_000_000).contains(step.frames), total <= 1_000_000 - step.frames else {
                throw VirtuaTennisError.message("A replay is limited to one million frames.")
            }
            total += step.frames
        }
    }
    func input(frame: Int) -> VTInput {
        var end = 0
        for step in steps { end += step.frames; if frame < end { return step.input } }
        return VTInput()
    }
}
