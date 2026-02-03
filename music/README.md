# Background Music Library

This directory contains background music files organized by mood. The video generator will automatically select appropriate music based on your video topic.

## Directory Structure

```
music/
├── energetic/    - Upbeat, fast-paced music for action/sport videos
├── calm/         - Relaxing, peaceful music for nature/meditation videos
├── inspiring/    - Motivational, uplifting music for success/achievement videos
└── neutral/      - General-purpose background music (default fallback)
```

## Getting Royalty-Free Music

You need to add your own music files (.mp3 or .wav) to these directories. Here are recommended sources:

### Free Sources (CC0/Public Domain)
1. **YouTube Audio Library**: https://studio.youtube.com/channel/audio_library
   - Filter by "Attribution not required"
   - Download and place in appropriate mood folder

2. **Incompetech**: https://incompetech.com/music/royalty-free/
   - Free with attribution (CC BY 4.0)
   - Add credit in video description

3. **Free Music Archive**: https://freemusicarchive.org/
   - Filter by CC0 license
   - Wide variety of genres

### Premium Sources (Paid)
1. **Epidemic Sound**: https://www.epidemicsound.com/
2. **Artlist**: https://artlist.io/
3. **AudioJungle**: https://audiojungle.net/

## File Naming

Use descriptive names for easy identification:
```
energetic/
  - upbeat_electronic_01.mp3
  - fast_drums_action.mp3

calm/
  - ambient_piano_peaceful.mp3
  - nature_sounds_ocean.mp3

inspiring/
  - motivational_strings_01.mp3
  - epic_orchestral_journey.mp3

neutral/
  - background_soft_01.mp3
  - general_purpose_calm.mp3
```

## Usage

The music selector will:
1. Auto-detect mood from your video topic keywords
2. Randomly select a track from the appropriate mood folder
3. Loop the music to match video duration
4. Mix it at 15% volume under narration (configurable in .env)

### Manual Mood Override

Set `MUSIC_MOOD` in .env to force a specific mood:
```bash
MUSIC_MOOD=calm        # Always use calm music
MUSIC_MOOD=energetic   # Always use energetic music
MUSIC_MOOD=auto        # Auto-detect from topic (default)
```

## Recommendations

- **Duration**: Music files should be at least 30 seconds (they will loop for longer videos)
- **Format**: .mp3 (recommended) or .wav
- **Volume**: Original files should have consistent volume levels
- **Quality**: 192kbps or higher for MP3
- **Quantity**: Add 3-5 tracks per mood category for variety

## Licensing

⚠️ **Important**: Ensure you have the right to use any music files you add. Always:
- Check the license before downloading
- Provide attribution if required
- Keep track of licenses for compliance
- Never use copyrighted music without permission

## Disabling Background Music

If you don't want background music, set in .env:
```bash
ENABLE_BACKGROUND_MUSIC=false
```
