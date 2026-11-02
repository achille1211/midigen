import Foundation

/// Original oscillator and drum voices. No downloaded samples or sound banks.
/// Rendering happens ahead of playback, away from the audio render callback.
enum SynthDSP {
    static let sampleRate = 44_100.0

    static func render(song: Song, sampleRate: Double = SynthDSP.sampleRate) -> [Float] {
        let length = Int((song.seconds + max(0.6, song.patch.release)) * sampleRate)
        var mix = [Float](repeating: 0, count: length)
        for (index, note) in song.notes.enumerated() {
            let start = Int(note.beat * 60 / song.bpm * sampleRate)
            let samples = renderNote(voice: note.voice, pitch: note.pitch, velocity: note.velocity,
                                     gate: note.duration * 60 / song.bpm, patch: song.patch,
                                     seed: song.seed &+ UInt64(index), sampleRate: sampleRate)
            guard start >= 0 && start < length else { continue }
            for offset in 0..<min(samples.count, length - start) { mix[start + offset] += samples[offset] }
        }
        for index in mix.indices { mix[index] = Float(tanh(Double(mix[index]) * 0.8)) * 0.9 }
        return mix
    }

    static func renderNote(voice: Voice, pitch: Int, velocity: Double, gate: Double,
                           patch: Patch, seed: UInt64 = 1, sampleRate: Double = SynthDSP.sampleRate) -> [Float] {
        let isDrum = [.kick, .snare, .hat, .clap].contains(voice)
        let duration = isDrum ? (voice == .hat ? 0.13 : 0.55) : max(0.02, gate) + patch.release
        let count = max(1, Int(duration * sampleRate))
        let frequency = 440 * pow(2, Double(pitch - 69) / 12)
        let level = min(1, max(0, velocity))
        var random = SeededRandom(seed: seed)
        var phase = 0.0
        var lowpass = 0.0
        var result = [Float](repeating: 0, count: count)
        for frame in 0..<count {
            let t = Double(frame) / sampleRate
            let noise = Double(random.next(65_536)) / 32_768 - 1
            var value: Double
            switch voice {
            case .kick:
                phase += 2 * .pi * (48 + 100 * exp(-t * 35)) / sampleRate
                value = sin(phase) * exp(-t * 11)
            case .snare:
                value = (noise * 0.78 + sin(2 * .pi * 180 * t) * 0.22) * exp(-t * 17)
            case .hat:
                lowpass += 0.15 * (noise - lowpass)
                value = (noise - lowpass) * exp(-t * 48)
            case .clap:
                let burst = t < 0.04 ? pow(sin(t * .pi / 0.013), 2) : exp(-(t - 0.04) * 25)
                value = noise * burst * exp(-t * 8)
            default:
                let waveform: Waveform = voice == .bass ? .sine : (voice == .keys ? .triangle : patch.waveform)
                value = oscillator(frequency: frequency, time: t, waveform: waveform, sampleRate: sampleRate)
                let attack = min(1, t / max(0.002, patch.attack))
                let decay = voice == .keys ? exp(-t * 1.8) : (0.7 + 0.3 * exp(-t * 8))
                let release = t <= gate ? 1 : max(0, 1 - (t - gate) / max(0.01, patch.release))
                value *= attack * decay * release
            }
            // A short fade prevents clicks at buffer boundaries, including percussion.
            let fade = min(1, min(Double(frame) / 32, Double(count - 1 - frame) / 64))
            result[frame] = Float(value * level * fade)
        }
        return result
    }

    private static func oscillator(frequency: Double, time: Double, waveform: Waveform, sampleRate: Double) -> Double {
        let phase = 2 * .pi * frequency * time
        if waveform == .sine { return sin(phase) }
        var value = 0.0
        for harmonic in 1...12 where frequency * Double(harmonic) < sampleRate * 0.45 {
            let h = Double(harmonic)
            if waveform == .saw {
                value += sin(phase * h) / h
            } else if harmonic % 2 == 1 {
                let sign = ((harmonic - 1) / 2) % 2 == 0 ? 1.0 : -1.0
                value += sign * sin(phase * h) / (h * h)
            }
        }
        return value * (waveform == .saw ? 0.5 : 0.8)
    }

    /// Standard little-endian PCM16 mono WAV, suitable for the Files/share sheet.
    static func wav(samples: [Float], sampleRate: Int = 44_100) -> Data {
        var data = Data()
        func text(_ value: String) { data.append(contentsOf: value.utf8) }
        func u16(_ value: UInt16) { data.append(UInt8(value & 255)); data.append(UInt8(value >> 8)) }
        func u32(_ value: UInt32) { u16(UInt16(value & 65_535)); u16(UInt16(value >> 16)) }
        let bytes = UInt32(samples.count * 2)
        text("RIFF"); u32(36 + bytes); text("WAVEfmt "); u32(16)
        u16(1); u16(1); u32(UInt32(sampleRate)); u32(UInt32(sampleRate * 2)); u16(2); u16(16)
        text("data"); u32(bytes)
        for sample in samples {
            let value = sample.isFinite ? min(1, max(-1, sample)) : 0
            u16(UInt16(bitPattern: Int16(value * 32_767)))
        }
        return data
    }
}
