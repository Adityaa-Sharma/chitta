"""shruti - that which is heard.

Voice, both directions.

In: Wispr Flow has no API and needs none. It types into whatever holds focus,
so a command that reads stdin already accepts dictation - in the terminal, in
the dashboard, anywhere. There is nothing to integrate.

Out: macOS `say`. 184 voices ship with the OS at zero RAM and zero
dependencies, which beats a 82M-parameter model for reading a sentence aloud.

ponytail: no Kokoro, no piper, no TTS process. Revisit only if `say` is
actually not good enough to listen to - and listen first before deciding.
"""
import shutil
import subprocess

VOICE = "Aman"   # en_IN, ships with macOS
RATE = 185       # words/min; the 175 default reads a shade slow


def available():
    return shutil.which("say") is not None


def speak(text, voice=VOICE, rate=RATE, block=True):
    """Say it. Returns False if `say` is missing rather than raising."""
    if not available():
        return False
    text = " ".join(str(text).split())
    if not text:
        return False
    cmd = ["say", "-v", voice, "-r", str(rate), text]
    (subprocess.run if block else subprocess.Popen)(cmd)
    return True


def voices(lang="en"):
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    return [l.split()[0] for l in out.splitlines() if f" {lang}" in l]
