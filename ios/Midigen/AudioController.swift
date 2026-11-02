import AVFoundation
import Combine
import Foundation

@MainActor
final class AudioController: ObservableObject {
    @Published var mood: Mood = .warm
    @Published var bpm = 108.0
    @Published var root = 0
    @Published var patch = Patch()
    @Published var cutoff = 8_000.0 { didSet { eq.bands[0].frequency = Float(cutoff) } }
    @Published var volume = 0.65 { didSet { engine.mainMixerNode.outputVolume = Float(volume) } }
    @Published private(set) var isGenerating = false
    @Published private(set) var isPlaying = false
    @Published private(set) var keysReady = false
    @Published private(set) var song: Song?
    @Published private(set) var exportURL: URL?
    @Published private(set) var message = "Tap Generate to create an eight-bar sketch."
    @Published var error: String?

    private let engine = AVAudioEngine()
    private let songPlayer = AVAudioPlayerNode()
    private let instrumentMixer = AVAudioMixerNode()
    private let eq = AVAudioUnitEQ(numberOfBands: 1)
    private let delay = AVAudioUnitDelay()
    private var livePlayers: [AVAudioPlayerNode] = []
    private var nextPlayer = 0
    private var cached: [String: AVAudioPCMBuffer] = [:]
    private var songBuffer: AVAudioPCMBuffer?
    private var observers: [NSObjectProtocol] = []
    private var playID = UUID()
    private var cacheID = UUID()
    private var generationID = UUID()
    private let format = AVAudioFormat(standardFormatWithSampleRate: SynthDSP.sampleRate, channels: 1)!

    init() {
        engine.attach(songPlayer)
        engine.attach(instrumentMixer)
        engine.attach(eq)
        engine.attach(delay)
        engine.connect(songPlayer, to: instrumentMixer, format: format)
        for _ in 0..<12 {
            let player = AVAudioPlayerNode()
            livePlayers.append(player)
            engine.attach(player)
            engine.connect(player, to: instrumentMixer, format: format)
        }
        eq.bands[0].filterType = .lowPass
        eq.bands[0].frequency = Float(cutoff)
        eq.bands[0].bypass = false
        delay.delayTime = 0.23
        delay.feedback = 18
        delay.wetDryMix = 12
        engine.connect(instrumentMixer, to: eq, format: format)
        engine.connect(eq, to: delay, format: format)
        engine.connect(delay, to: engine.mainMixerNode, format: format)
        engine.mainMixerNode.outputVolume = Float(volume)
        for name in [AVAudioSession.interruptionNotification, AVAudioSession.routeChangeNotification,
                     .AVAudioEngineConfigurationChange] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.stop()
                    self?.message = "Audio route changed or playback was interrupted. Tap Play to resume."
                }
            })
        }
    }

    deinit { observers.forEach { NotificationCenter.default.removeObserver($0) } }

    func activate() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try session.setPreferredSampleRate(SynthDSP.sampleRate)
            try session.setPreferredIOBufferDuration(0.005)
            try session.setActive(true)
            if !engine.isRunning { engine.prepare(); try engine.start() }
        } catch { self.error = "Could not start audio: \(error.localizedDescription)" }
    }

    func prepareKeys() async {
        let id = UUID()
        cacheID = id
        keysReady = false
        let selectedPatch = patch
        let rendered = await Task.detached(priority: .userInitiated) {
            var results: [String: [Float]] = [:]
            for pitch in 60...71 {
                results["lead-\(pitch)"] = SynthDSP.renderNote(voice: .lead, pitch: pitch, velocity: 0.5,
                                                            gate: 0.55, patch: selectedPatch)
            }
            for voice in [Voice.kick, .snare, .hat, .clap] {
                results[voice.rawValue] = SynthDSP.renderNote(voice: voice, pitch: 36, velocity: 0.6,
                                                            gate: 0.2, patch: selectedPatch)
            }
            return results
        }.value
        guard cacheID == id else { return }
        cached = rendered.compactMapValues { buffer($0) }
        keysReady = true
    }

    func hit(_ voice: Voice, pitch: Int = 60) {
        guard keysReady else { return }
        activate()
        guard engine.isRunning,
              let sound = cached[voice == .lead ? "lead-\(pitch)" : voice.rawValue] else { return }
        let player = livePlayers[nextPlayer % livePlayers.count]
        nextPlayer += 1
        player.stop()
        player.scheduleBuffer(sound)
        player.play()
    }

    func generate() async {
        guard !isGenerating else { return }
        stop()
        isGenerating = true
        defer { isGenerating = false }
        let id = UUID()
        generationID = id
        let draft = SongGenerator.generate(seed: UInt64.random(in: 1...UInt64.max), bpm: bpm,
                                           root: root, mood: mood, patch: patch)
        message = "Rendering your sketch on this iPhone…"
        let result = await Task.detached(priority: .userInitiated) {
            let samples = SynthDSP.render(song: draft)
            return (samples, SynthDSP.wav(samples: samples))
        }.value
        guard generationID == id else { return }
        do {
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("MIDIGEN-sketch.wav")
            try result.1.write(to: url, options: .atomic)
            songBuffer = buffer(result.0)
            song = draft
            exportURL = url
            message = "Eight bars ready · \(Int(draft.bpm)) BPM · \(draft.mood.rawValue.lowercased())"
        } catch { self.error = "Could not export the sketch: \(error.localizedDescription)" }
    }

    func play() {
        guard let songBuffer else { return }
        activate()
        guard engine.isRunning else { return }
        stop()
        let id = UUID()
        playID = id
        songPlayer.scheduleBuffer(songBuffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard self?.playID == id else { return }
                self?.isPlaying = false
            }
        }
        songPlayer.play()
        isPlaying = true
    }

    func stop() {
        playID = UUID()
        songPlayer.stop()
        livePlayers.forEach { $0.stop() }
        isPlaying = false
    }

    func suspend() {
        stop()
        engine.pause()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func saveProject() {
        guard let song else { return }
        do {
            let documents = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                                        appropriateFor: nil, create: true)
            let data = try JSONEncoder().encode(song)
            try data.write(to: documents.appendingPathComponent("LastSketch.json"), options: .atomic)
            message = "Project saved on this iPhone."
        } catch { self.error = "Could not save: \(error.localizedDescription)" }
    }

    func loadProject() async {
        guard !isGenerating else { return }
        do {
            let documents = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                                        appropriateFor: nil, create: true)
            let data = try Data(contentsOf: documents.appendingPathComponent("LastSketch.json"))
            let saved = try JSONDecoder().decode(Song.self, from: data)
            guard saved.bpm >= 60 && saved.bpm <= 160 && saved.beats == 32 && saved.notes.count <= 1024,
                  (0...11).contains(saved.root), saved.patch.attack >= 0.003 && saved.patch.attack <= 0.2,
                  saved.patch.release >= 0.05 && saved.patch.release <= 0.8,
                  saved.notes.allSatisfy({ $0.beat >= 0 && $0.beat < 32 && $0.duration > 0 && $0.duration <= 8 &&
                      (0...127).contains($0.pitch) && $0.velocity >= 0 && $0.velocity <= 1 }) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            stop()
            isGenerating = true
            defer { isGenerating = false }
            let result = await Task.detached(priority: .userInitiated) {
                let samples = SynthDSP.render(song: saved)
                return (samples, SynthDSP.wav(samples: samples))
            }.value
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("MIDIGEN-sketch.wav")
            try result.1.write(to: url, options: .atomic)
            mood = saved.mood; bpm = saved.bpm; root = saved.root; patch = saved.patch
            song = saved; songBuffer = buffer(result.0); exportURL = url
            await prepareKeys()
            message = "Saved project restored."
        } catch { self.error = "Could not open a saved project: \(error.localizedDescription)" }
    }

    private func buffer(_ samples: [Float]) -> AVAudioPCMBuffer? {
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let destination = buffer.floatChannelData?[0] else { return nil }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { source in
            if let base = source.baseAddress { destination.update(from: base, count: samples.count) }
        }
        return buffer
    }
}
