"""Generate WAV audio files for each TUTORIAL_STEP using Google Cloud TTS."""

from pathlib import Path
from google.cloud import texttospeech_v1beta1 as texttospeech

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "oral_instructions"

TUTORIAL_TEXTS = [
    "Welcome to the practice! Let me show you the animations I can do. When you see one, it means I want you to say or repeat something about the picture! No need to remember them, I just want you to see them at least once!",
    "If I want you to talk about a character, I will shine a spotlight on them, like this!",
    "If I am not sure who or what you are talking about, things will glow one by one!",
    "I can also show a name tag floating above them if they need a name, or want to be called by it!",
    "When I want you to describe how someone is moving, I show lines of movement!",
    "I can also flip them around to show they are in action!",
    "If I want you to describe what something looks like, I will make its colors pop!",
    "When I want you to tell me how someone is feeling, I show little particles around them!",
    "If something is hiding behind another thing, I can make it see-through so you can see!",
    "I can also stamp a character to show where they are standing!",
    "If I want you to use the past tense, the picture will look like an old movie!",
    "And if I want you to talk about the future, you will see a day-and-night effect!",
    "When two characters are connected, they will be pulled toward each other!",
    "And if they should be apart, they will push away from each other!",
    "When one thing causes something to happen to another, you will see a push!",
    "When something should not be mentioned, it will break apart into tiny pieces!",
    "And if something is missing, you will see a ghostly shape!",
    "If I want a character to say something, a speech bubble will appear!",
    "If I want a character to think something, a thought bubble will appear!",
    "When something important or surprising happens, you will see an exclamation mark!",
    "And when a word is really special, it will burst out like in a comic book!",
    "Great job! Now you know all my animations. Let's practice with the next scene!",
]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = texttospeech.TextToSpeechClient()

    for i, text in enumerate(TUTORIAL_TEXTS, start=1):
        filename = f"tutorial_{i:02d}.wav"
        out_path = OUTPUT_DIR / filename

        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Chirp3-HD-Gacrux",
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            ),
        )

        out_path.write_bytes(response.audio_content)
        print(f"[{i:2d}/22] {filename} ({len(response.audio_content)} bytes)")

    print(f"\nDone — {len(TUTORIAL_TEXTS)} files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
