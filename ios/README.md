# MIDIGEN for iPhone — native prototype

An iOS 17+ SwiftUI app with original synthesized instruments, offline eight-bar composition,
tap-to-play keys, four drum pads, tone/master controls, WAV sharing and one local project slot.
No Python runtime, network service, microphone permission or downloaded sound bank is required.

## Run on a Mac

1. Open `ios/Midigen.xcodeproj` in Xcode 16 or newer with an iOS SDK installed.
2. Select the **Midigen** scheme and an iPhone simulator, then Run.
3. For a physical iPhone, select your own development team under Signing & Capabilities, choose
   a unique bundle identifier if needed, and select the connected device. Apple signing and device
   provisioning remain under your control; no credentials or signing team are stored here.
4. Tap **Generate eight bars**, then **Play**. Tap the synth keys and drum pads to audition sounds.
5. Use **WAV** to open the system share sheet. **Save project** stores `LastSketch.json` locally;
   **Open saved** restores it. Saving again replaces the single saved project.

Apple's Xcode platform requirements: https://developer.apple.com/xcode/system-requirements

The current development workspace is Windows and cannot run Xcode or the iOS Simulator.
The GitHub **iPhone prototype** workflow runs native core tests and builds/runs a simulator UI
smoke test on macOS. A green workflow is build/simulator evidence; hardware latency, sound quality,
audio-route interruptions and battery use still need testing on a real iPhone.

## What is implemented

- Three synth waveforms (soft sine, warm triangle, bright additive saw), attack/release controls,
  synthesized keys/bass and deterministic kick/snare/hat/clap voices.
- An eight-bar native arranger with major/minor harmony, bass, melody and a drum pattern.
- Audio buffers rendered off the main thread for composition. Instrument buffers are prepared
  ahead of taps and played through a pool of AVAudioPlayerNodes.
- A playback low-pass filter, light delay and master volume.
- Dry mono PCM16 WAV export and a Codable native project file.
- Stop/pause when leaving the app; interruption/route changes stop playback and allow manual resume.

The WAV contains the dry generated sketch. It excludes live taps, the listening filter/delay and
master volume changes. Patch changes apply to new keyboard taps; regenerate to bake them into a song.
The keyboard plays short one-shot notes, not held or pressure-sensitive notes.

## Scope and next integration

This is an iPhone product prototype, not the complete Python MIDIGEN application ported to iOS.
The native project JSON has a different schema from the Python song spec. Plain-language editing,
the full genre library, MIDI import/export, multitrack editing, recording live performances,
seamless looping and cloud sync are not implemented here. No sampled acoustic piano is bundled;
the Keys voice is synthesized. The app has no App Store icon/assets, TestFlight signing or
App Store submission configuration yet.

Next steps after device audition: improve voice quality, add a shared Python/native event interchange,
MIDI export, a project library, and frame-accurate live sequencing/automation. Real-time instrument
controls require a dedicated DSP render path rather than re-rendering song buffers.

## Checks

```sh
swift test --package-path ios
xcodebuild test -project ios/Midigen.xcodeproj -scheme Midigen \
  -destination 'platform=iOS Simulator,name=YOUR_INSTALLED_IPHONE' CODE_SIGNING_ALLOWED=NO
```

Core checks cover seeded generation, minor harmony, note bounds, non-silent finite oscillator/drum
output, WAV encoding and project round trips. The simulator smoke test generates, plays/stops,
saves and restores a sketch. Listen on hardware before making sound-quality or latency claims.
