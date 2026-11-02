import Foundation
import XCTest
@testable import MidigenCore

final class MidigenCoreTests: XCTestCase {
    func testGeneratorIsReproducibleAndKeepsNotesInsideEightBars() {
        let song = SongGenerator.generate(seed: 42, bpm: 108, root: 0, mood: .warm, patch: Patch())
        XCTAssertEqual(song, SongGenerator.generate(seed: 42, bpm: 108, root: 0, mood: .warm, patch: Patch()))
        XCTAssertNotEqual(song.notes, SongGenerator.generate(seed: 43, bpm: 108, root: 0, mood: .warm, patch: Patch()).notes)
        XCTAssertEqual(song.beats, 32)
        XCTAssertTrue(song.notes.allSatisfy { $0.beat >= 0 && $0.beat + $0.duration <= 32 })
        XCTAssertEqual(Set(song.notes.map(\.voice)), [.keys, .bass, .lead, .kick, .snare, .hat])
    }

    func testDarkSketchHasMinorTonicHarmony() {
        let song = SongGenerator.generate(seed: 1, bpm: 90, root: 0, mood: .dark, patch: Patch())
        let firstChord = song.notes.filter { $0.voice == .keys && $0.beat == 0 }.map(\.pitch).sorted()
        XCTAssertEqual(firstChord, [60, 63, 67])
    }

    func testSynthOutputsFiniteBoundedNonSilentAudio() {
        for voice in Voice.allCases {
            let samples = SynthDSP.renderNote(voice: voice, pitch: 60, velocity: 0.5, gate: 0.3, patch: Patch(), sampleRate: 8_000)
            XCTAssertTrue(samples.allSatisfy { $0.isFinite && abs($0) <= 1 })
            XCTAssertGreaterThan(samples.map { abs($0) }.max() ?? 0, 0.01)
            XCTAssertEqual(samples.first, 0)
            XCTAssertEqual(samples.last, 0)
        }
    }

    func testRenderedSongAndWAVHaveCorrectDurationAndHeader() {
        let song = SongGenerator.generate(seed: 8, bpm: 120, root: 5, mood: .bright, patch: Patch())
        let samples = SynthDSP.render(song: song, sampleRate: 8_000)
        XCTAssertEqual(samples.count, Int(16.6 * 8_000))
        XCTAssertTrue(samples.allSatisfy { $0.isFinite && abs($0) < 1 })
        let wav = SynthDSP.wav(samples: samples, sampleRate: 8_000)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .utf8), "RIFF")
        XCTAssertEqual(String(data: wav[8..<12], encoding: .utf8), "WAVE")
        XCTAssertEqual(wav.count, 44 + samples.count * 2)
        XCTAssertEqual(Array(wav[24..<28]), [0x40, 0x1f, 0, 0])
        XCTAssertEqual(try JSONDecoder().decode(Song.self, from: JSONEncoder().encode(song)), song)
    }

    func testWaveformsProduceDifferentTimbres() {
        let soft = SynthDSP.renderNote(voice: .lead, pitch: 60, velocity: 0.5, gate: 0.2,
                                       patch: Patch(waveform: .sine), sampleRate: 8_000)
        let bright = SynthDSP.renderNote(voice: .lead, pitch: 60, velocity: 0.5, gate: 0.2,
                                         patch: Patch(waveform: .saw), sampleRate: 8_000)
        XCTAssertNotEqual(soft, bright)
    }
}
