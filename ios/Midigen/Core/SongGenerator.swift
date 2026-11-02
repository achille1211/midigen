import Foundation

enum Voice: String, Codable, CaseIterable, Sendable {
    case keys, bass, lead, kick, snare, hat, clap
}

enum Waveform: String, Codable, CaseIterable, Sendable {
    case sine = "Soft", triangle = "Warm", saw = "Bright"
}

enum Mood: String, Codable, CaseIterable, Sendable {
    case warm = "Warm", dark = "Dark", bright = "Bright"
}

struct Patch: Codable, Equatable, Sendable {
    var waveform: Waveform = .triangle
    var attack: Double = 0.012
    var release: Double = 0.25
}

struct NoteEvent: Codable, Equatable, Sendable {
    var beat: Double
    var duration: Double
    var pitch: Int
    var velocity: Double
    var voice: Voice
}

struct Song: Codable, Equatable, Sendable {
    var seed: UInt64
    var bpm: Double
    var root: Int
    var mood: Mood
    var patch: Patch
    var notes: [NoteEvent]
    var beats: Double = 32
    var seconds: Double { beats * 60 / bpm }
}

struct SeededRandom {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next(_ count: Int) -> Int {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return Int((state >> 32) % UInt64(count))
    }
}

enum SongGenerator {
    /// A small native sketch generator; the full Python composer is not embedded.
    static func generate(seed: UInt64, bpm: Double, root: Int, mood: Mood, patch: Patch) -> Song {
        let tempo = min(160, max(60, bpm))
        let tonic = min(11, max(0, root))
        let minor = mood == .dark
        let scale = minor ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11]
        let degrees = [0, 5, 3, 4]
        var rng = SeededRandom(seed: seed)
        var notes: [NoteEvent] = []
        func add(_ voice: Voice, _ beat: Double, _ duration: Double, _ pitch: Int, _ velocity: Double) {
            notes.append(NoteEvent(beat: beat, duration: duration, pitch: pitch, velocity: velocity, voice: voice))
        }
        for bar in 0..<8 {
            let start = Double(bar * 4)
            let degree = degrees[bar % degrees.count]
            let chord = [0, 2, 4].map { step -> Int in
                let index = degree + step
                return 60 + tonic + scale[index % 7] + (index / 7) * 12
            }
            for pitch in chord { add(.keys, start, 3.5, pitch, 0.20) }
            for beat in 0..<4 {
                add(.bass, start + Double(beat), 0.7, 36 + tonic + scale[degree], beat == 0 ? 0.50 : 0.35)
            }
            for step in 0..<8 {
                let position = start + Double(step) * 0.5
                add(.hat, position, 0.08, 42, step % 2 == 0 ? 0.20 : 0.12)
                if step == 0 || step == 4 { add(.kick, position, 0.3, 36, 0.65) }
                if step == 2 || step == 6 { add(.snare, position, 0.2, 38, 0.32) }
                if step < 6 && (mood == .bright || rng.next(4) > 0) {
                    let pitch = chord[rng.next(chord.count)] + (rng.next(4) == 0 ? 12 : 0)
                    add(.lead, position, mood == .dark ? 0.65 : 0.35, pitch, 0.22)
                }
            }
        }
        return Song(seed: seed, bpm: tempo, root: tonic, mood: mood, patch: patch,
                    notes: notes.sorted { $0.beat < $1.beat })
    }
}
