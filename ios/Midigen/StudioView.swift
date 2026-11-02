import SwiftUI

struct StudioView: View {
    @StateObject private var audio = AudioController()
    @Environment(\.scenePhase) private var scenePhase
    private let accent = Color(red: 0.69, green: 0.60, blue: 1)
    private let noteNames = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("OFFLINE POCKET STUDIO", systemImage: "waveform")
                            .font(.caption.weight(.bold)).tracking(2).foregroundStyle(accent)
                        Text("Make something\nworth keeping.").font(.largeTitle.bold())
                        Text("An eight-bar idea. Your own instruments. No connection needed.")
                            .foregroundStyle(.secondary)
                    }
                    ideaCard
                    synthCard
                    drumCard
                    finishCard
                }.padding(20)
            }
            .background(Color(red: 0.055, green: 0.06, blue: 0.085))
            .navigationTitle("MIDIGEN").navigationBarTitleDisplayMode(.inline).tint(accent)
            .task { audio.activate(); await audio.prepareKeys() }
            .onChange(of: audio.patch.waveform) { _, _ in Task { await audio.prepareKeys() } }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { audio.activate() } else { audio.suspend() }
            }
            .alert("Audio studio", isPresented: Binding(get: { audio.error != nil }, set: { if !$0 { audio.error = nil } })) {
                Button("OK") { audio.error = nil }
            } message: { Text(audio.error ?? "") }
        }
    }

    private var ideaCard: some View {
        card {
            Text("01 / THE IDEA").font(.caption.monospaced()).foregroundStyle(accent)
            Picker("Mood", selection: $audio.mood) {
                ForEach(Mood.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented)
            HStack {
                Text("Key")
                Spacer()
                Picker("Key", selection: $audio.root) {
                    ForEach(0..<12, id: \.self) { Text(noteNames[$0]).tag($0) }
                }.labelsHidden()
                Text(audio.mood == .dark ? "minor" : "major").foregroundStyle(.secondary)
            }
            HStack { Text("Tempo"); Spacer(); Text("\(Int(audio.bpm)) BPM").monospacedDigit() }
            Slider(value: $audio.bpm, in: 60...160, step: 1).accessibilityLabel("Tempo")
            Button {
                Task { await audio.generate() }
            } label: {
                HStack {
                    if audio.isGenerating { ProgressView() } else { Image(systemName: "sparkles") }
                    Text(audio.isGenerating ? "Making your sketch…" : "Generate eight bars")
                }.frame(maxWidth: .infinity).padding(.vertical, 8)
            }.buttonStyle(.borderedProminent).disabled(audio.isGenerating)
            HStack {
                Button(audio.isPlaying ? "Stop" : "Play", systemImage: audio.isPlaying ? "stop.fill" : "play.fill") {
                    audio.isPlaying ? audio.stop() : audio.play()
                }.disabled(audio.song == nil || audio.isGenerating)
                Spacer()
                if let url = audio.exportURL { ShareLink(item: url) { Label("WAV", systemImage: "square.and.arrow.up") } }
            }.buttonStyle(.bordered)
            Text(audio.message).font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("studioStatus")
            Text("WAV shares the dry sketch; the listening filter and delay are not baked into it.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var synthCard: some View {
        card {
            Text("02 / YOUR SYNTH").font(.caption.monospaced()).foregroundStyle(accent)
            Picker("Sound", selection: $audio.patch.waveform) {
                ForEach(Waveform.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented)
            HStack { Text("Attack"); Spacer(); Text("\(Int(audio.patch.attack * 1000)) ms").monospacedDigit() }
            Slider(value: $audio.patch.attack, in: 0.003...0.2) { editing in
                if !editing { Task { await audio.prepareKeys() } }
            }.accessibilityLabel("Synth attack")
            HStack { Text("Release"); Spacer(); Text("\(Int(audio.patch.release * 1000)) ms").monospacedDigit() }
            Slider(value: $audio.patch.release, in: 0.05...0.8) { editing in
                if !editing { Task { await audio.prepareKeys() } }
            }.accessibilityLabel("Synth release")
            Text(audio.keysReady ? "Tap a key to play" : "Preparing instruments…").font(.footnote).foregroundStyle(.secondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 5) {
                    ForEach(0..<12, id: \.self) { index in
                        Button { audio.hit(.lead, pitch: 60 + index) } label: {
                            Text(noteNames[index]).font(.caption.bold()).foregroundStyle(.black)
                                .frame(width: 43, height: index == 1 || index == 3 || index == 6 || index == 8 || index == 10 ? 78 : 108)
                                .background(index == 1 || index == 3 || index == 6 || index == 8 || index == 10 ? accent : .white,
                                            in: RoundedRectangle(cornerRadius: 8))
                        }.accessibilityLabel("Play \(noteNames[index]) four").disabled(!audio.keysReady)
                    }
                }.frame(height: 110, alignment: .top)
            }
            Text("Patch changes shape new key taps. Generate again to apply them to a song.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var drumCard: some View {
        card {
            Text("03 / THE BEAT").font(.caption.monospaced()).foregroundStyle(accent)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                ForEach([Voice.kick, .snare, .hat, .clap], id: \.self) { voice in
                    Button { audio.hit(voice) } label: {
                        Text(voice.rawValue.capitalized).font(.headline).frame(maxWidth: .infinity).frame(height: 68)
                    }.buttonStyle(.bordered).tint(accent).disabled(!audio.keysReady)
                }
            }
        }
    }

    private var finishCard: some View {
        card {
            Text("04 / FINISH").font(.caption.monospaced()).foregroundStyle(accent)
            HStack { Text("Tone"); Spacer(); Text("\(Int(audio.cutoff)) Hz").monospacedDigit() }
            Slider(value: $audio.cutoff, in: 200...18_000).accessibilityLabel("Tone cutoff")
            HStack { Text("Master"); Spacer(); Text("\(Int(audio.volume * 100))%").monospacedDigit() }
            Slider(value: $audio.volume, in: 0...1).accessibilityLabel("Master volume")
            HStack {
                Button("Save project", systemImage: "square.and.arrow.down") { audio.saveProject() }
                    .disabled(audio.song == nil || audio.isGenerating)
                Spacer()
                Button("Open saved", systemImage: "folder") { Task { await audio.loadProject() } }
                    .disabled(audio.isGenerating)
            }.buttonStyle(.bordered)
            Text("Prototype · one local project slot · original synthesized sounds")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 14, content: content)
            .padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.07)))
    }
}
