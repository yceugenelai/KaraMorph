"""Optional LV-Chordia worker; one result file, no UI-process model imports."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runtime_config import worker_env
os.environ.update(worker_env())
os.environ['CUDA_VISIBLE_DEVICES'] = ''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    import numpy as np
    import librosa
    from lv_chordia.chord_recognition import chord_recognition
    from app.plugins.guitar.optimize import analyze, NOTES
    from app.plugins.guitar.data import source_signature
    audio = Path(args.audio).resolve()
    initial = audio.stat()
    y, sr = librosa.load(audio, sr=22050, mono=True)
    if len(y) < sr // 2:
        raise ValueError('Audio is shorter than 0.5 seconds')
    hop = 512
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop, trim=False)
    duration = len(y) / sr
    times = librosa.frames_to_time(np.arange(int(np.ceil(len(y) / hop))), sr=sr, hop_length=hop)
    output = chord_recognition(str(audio))
    def convert(value):
        if value in ('N', 'X'): return 'N'
        root, _, quality = value.partition(':')
        root = {'Db':'C#', 'Eb':'D#', 'Gb':'F#', 'Ab':'G#', 'Bb':'A#'}.get(root, root)
        if root not in NOTES: return 'N'
        return root + {'maj':'', 'min':'m', 'min7':'m7'}.get(quality, quality)
    raw = [{'start':max(0., float(row['start_time'])), 'end':min(duration, float(row['end_time'])),
            'chord':convert(row['chord'])} for row in output if float(row['start_time']) < duration]
    if not raw:
        raise ValueError('Chord model returned no segments')
    labels = list(dict.fromkeys(s['chord'] for s in raw if s['chord'] != 'N')) + ['N']
    votes = np.zeros((len(labels), len(times)), dtype=np.float32)
    for segment in raw:
        votes[labels.index(segment['chord']), (times >= segment['start']) & (times < segment['end'])] = 1.
    votes[labels.index('N'), votes.sum(axis=0) == 0] = 1.
    data = {'labels':labels, 'scores':votes, 'times':times, 'beats':librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop),
            'duration':duration, 'tempo':float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 0,
            'timing':{}, 'engine':'C1', 'raw_segments':raw, 'raw_model':output}
    result = analyze(data)
    result['analysis'] = {'beats':data['beats'].tolist(), 'hop_seconds':hop / sr, 'strength':1.0}
    current = audio.stat()
    if (initial.st_size, initial.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
        raise ValueError('Audio changed while processing')
    result['source'] = source_signature(audio)
    result['schema'] = 1
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')

if __name__ == '__main__':
    main()
