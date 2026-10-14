"""Prepare the verified official SoundTouch DLL for desktop playback."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.soundtouch import ready, prepare, SoundTouch

if __name__ == '__main__':
    if not ready():
        prepare()
    processor = SoundTouch(48000, 4)
    processor.close()
    print('SoundTouch 2.3.3 x64 is ready')
