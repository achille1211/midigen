"""UI-layer data for newcomers: mood/use-case presets and plain-language labels.

Nothing here changes the generator. Presets still send their prompt text through the library's prompt entry point
(midigen.llm.Composer(...).generate, exactly what `midigen new "<prompt>"` calls offline); their explicit settings
are then applied on top, so a preset gives the intended genre/mood/length even when the offline parser does not
recognise the words. Genres missing from PLAIN_GENRES fall back to the library's own name/description.
"""

# id -> label, prompt text, explicit settings. seconds = target length (bars are computed from it and the tempo).
# programs: General MIDI program per role, applied with the library's set_track op (e.g. 80 = square lead).
PRESETS = [
    {"id": "calm_focus", "label": "Calm focus", "emoji": "🧘", "prompt": "calm focus music for studying",
     "settings": {"genre": "ambient", "mode": "major", "tempo": 66, "seconds": 60}},
    {"id": "boss_fight", "label": "Tense boss fight", "emoji": "⚔️", "prompt": "fast, tense cinematic boss fight music",
     "settings": {"genre": "cinematic", "mode": "harmonic_minor", "tempo": 140, "seconds": 45}},
    {"id": "coffee_shop", "label": "Chill coffee shop", "emoji": "☕", "prompt": "chill coffee shop music",
     "settings": {"genre": "lofi_hiphop", "mode": "major", "tempo": 82, "seconds": 60}},
    {"id": "upbeat_vlog", "label": "Upbeat vlog", "emoji": "🎬", "prompt": "upbeat pop for a vlog",
     "settings": {"genre": "pop", "mode": "major", "tempo": 124, "seconds": 30}},
    {"id": "sad_piano", "label": "Sad piano", "emoji": "🎹", "prompt": "just piano, sad, slow",
     "settings": {"genre": "pop", "mode": "minor", "tempo": 72, "seconds": 45, "roles": ["chords", "lead"],
                  "programs": {"chords": 0, "lead": 0}}},
    {"id": "epic_trailer", "label": "Epic trailer", "emoji": "🎥", "prompt": "epic trailer music",
     "settings": {"genre": "cinematic", "mode": "minor", "tempo": 96, "seconds": 45}},
    {"id": "podcast_intro", "label": "Podcast intro (15 s)", "emoji": "🎙️", "prompt": "upbeat intro for my podcast, 15 seconds",
     "settings": {"genre": "pop", "mode": "major", "tempo": 128, "seconds": 15}},
    {"id": "lullaby", "label": "Lullaby", "emoji": "🌙", "prompt": "gentle lullaby, no drums",
     "settings": {"genre": "ambient", "mode": "major", "tempo": 58, "seconds": 45, "exclude_roles": ["drums"]}},
    {"id": "horror", "label": "Horror", "emoji": "👻", "prompt": "dark slow horror music",
     "settings": {"genre": "cinematic", "mode": "harmonic_minor", "tempo": 62, "seconds": 45}},
    {"id": "eight_bit", "label": "8-bit game", "emoji": "👾", "prompt": "8-bit game menu loop",
     "settings": {"genre": "synthwave", "mode": "major", "tempo": 120, "seconds": 20,
                  "programs": {"lead": 80, "arp": 80, "bass": 81}}},
    {"id": "fanfare", "label": "Victory fanfare", "emoji": "🏆", "prompt": "victory fanfare for when the player wins",
     "settings": {"genre": "cinematic", "mode": "major", "tempo": 110, "seconds": 10}},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

# Prompts the help text and the "didn't understand" message suggest (all verified to resolve to a genre offline).
EXAMPLE_PROMPTS = ["chill coffee shop music", "epic trailer music", "calm focus music for studying",
                   "retro 80s driving music", "dance music, 30 seconds"]

PLAIN_GENRES = {
    "ambient": ("Ambient", "Slow, floating background music with long soft sounds."),
    "bigband": ("Big band swing", "A large jazz band with horns: lively, old-school and brassy."),
    "bossa_nova": ("Bossa nova", "Gentle Brazilian guitar music: relaxed, warm, café feel."),
    "cinematic": ("Cinematic / orchestral", "Movie-style music with strings, brass and big drums."),
    "drum_and_bass": ("Drum and bass", "Very fast, energetic electronic beats with deep bass."),
    "edm": ("EDM / festival dance", "Big, bright dance music that builds up and drops."),
    "emo": ("Emo", "Emotional guitar-band music, from gentle to loud."),
    "funk": ("Funk", "Groovy, danceable music with a tight bass and rhythm guitar."),
    "house": ("House", "Steady dance beat (a kick on every beat), smooth and upbeat."),
    "jazz": ("Jazz (swing)", "Swinging small-band jazz: walking bass, ride cymbal, piano."),
    "jazz_combo": ("Jazz combo", "A small jazz group playing a tune, solos, then the tune again."),
    "lofi_hiphop": ("Lo-fi hip hop", "Mellow, dusty beats for studying or relaxing."),
    "metal": ("Metal", "Heavy, loud and fast distorted guitars and drums."),
    "neo_soul": ("Neo-soul", "Laid-back, warm soul with rich keyboard chords."),
    "pop": ("Pop", "Catchy, radio-style song with a clear melody."),
    "pop_studio": ("Pop (produced)", "Modern polished pop where parts come in and out."),
    "reggae": ("Reggae", "Relaxed Jamaican groove with off-beat guitar chops."),
    "rock": ("Rock", "Guitar band with a strong steady beat."),
    "rock_band": ("Rock band", "Guitar riffs and power chords over a steady beat."),
    "screamo": ("Screamo", "Intense, chaotic punk-rooted music with big loud-quiet swings."),
    "synthwave": ("Synthwave / 80s retro", "1980s-style synths and drums, driving and nostalgic."),
    "techno": ("Techno", "Hypnotic, repetitive club beat for long dance sets."),
    "trap": ("Trap", "Modern hip-hop beat: booming bass and fast hi-hats."),
}

PLAIN_MODES = {
    "major": "bright / happy", "minor": "darker / sadder", "dorian": "cool, a little melancholy",
    "phrygian": "dark, tense, Spanish-tinged", "lydian": "dreamy, floating", "mixolydian": "bluesy, laid-back bright",
    "locrian": "very unstable, eerie", "harmonic_minor": "dramatic, exotic dark", "aeolian": "darker / sadder",
    "melodic_minor": "smooth, jazzy dark",
}


def plain_genre(gid: str, display: str, description: str) -> tuple[str, str]:
    lab, desc = PLAIN_GENRES.get(gid, (display or gid.replace("_", " ").title(), description))
    return lab, desc


def plain_mode(mode: str) -> str:
    return PLAIN_MODES.get(mode, mode.replace("_", " "))
