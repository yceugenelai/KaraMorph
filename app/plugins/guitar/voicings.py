"""Small, explicit set of standard-tuning guitar shapes, frets relative to capo."""
import re
from dataclasses import dataclass

NOTES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
FLATS = {'Db':'C#','Eb':'D#','Gb':'F#','Ab':'G#','Bb':'A#'}

@dataclass(frozen=True)
class Voicing:
    frets: tuple  # low E to high E; None = muted
    fingers: tuple  # 0 = open or muted; 1–4 = index to little finger

OPEN = {
    'C': ((None,3,2,0,1,0),(0,3,2,0,1,0)),
    'D': ((None,None,0,2,3,2),(0,0,0,1,3,2)),
    'E': ((0,2,2,1,0,0),(0,2,3,1,0,0)),
    'F': ((1,3,3,2,1,1),(1,3,4,2,1,1)),
    'G': ((3,2,0,0,0,3),(2,1,0,0,0,3)),
    'A': ((None,0,2,2,2,0),(0,0,1,2,3,0)),
    'Am': ((None,0,2,2,1,0),(0,0,2,3,1,0)),
    'Dm': ((None,None,0,2,3,1),(0,0,0,2,3,1)),
    'Em': ((0,2,2,0,0,0),(0,2,3,0,0,0)),
    'C7': ((None,3,2,3,1,0),(0,3,2,4,1,0)),
    'D7': ((None,None,0,2,1,2),(0,0,0,2,1,3)),
    'E7': ((0,2,0,1,0,0),(0,2,0,1,0,0)),
    'G7': ((3,2,0,0,0,1),(3,2,0,0,0,1)),
    'A7': ((None,0,2,0,2,0),(0,0,1,0,2,0)),
    'Am7': ((None,0,2,0,1,0),(0,0,2,0,1,0)),
    'Dm7': ((None,None,0,2,1,1),(0,0,0,2,1,1)),
    'Em7': ((0,2,0,0,0,0),(0,2,0,0,0,0)),
    'Cmaj7': ((None,3,2,0,0,0),(0,3,2,0,0,0)),
    'Dmaj7': ((None,None,0,2,2,2),(0,0,0,1,1,1)),
    'Emaj7': ((0,2,1,1,0,0),(0,2,1,1,0,0)),
    'Amaj7': ((None,0,2,1,2,0),(0,0,2,1,3,0)),
    'Asus2': ((None,0,2,2,0,0),(0,0,1,2,0,0)),
    'Asus4': ((None,0,2,2,3,0),(0,0,1,2,3,0)),
    'Dsus2': ((None,None,0,2,3,0),(0,0,0,1,3,0)),
    'Dsus4': ((None,None,0,2,3,3),(0,0,0,1,3,4)),
    'Cadd9': ((None,3,2,0,3,3),(0,2,1,0,3,4)),
    'Gadd9': ((3,2,0,2,0,3),(2,1,0,3,0,4)),
    'C/E': ((0,3,2,0,1,0),(0,3,2,0,1,0)),
    'C/G': ((3,3,2,0,1,0),(3,4,2,0,1,0)),
    'G/B': ((None,2,0,0,0,3),(0,1,0,0,0,3)),
    'D/F#': ((None,None,4,2,3,2),(0,0,3,1,2,1)),
    'Am/G': ((3,None,2,2,1,0),(3,0,2,4,1,0)),
}

# Shifted E- and A-family shapes. The first finger forms a movable barre.
MOVABLE = {
    '': [((0,2,2,1,0,0),(1,3,4,2,1,1),'E'),
         ((None,0,2,2,2,0),(0,1,3,3,3,1),'A')],
    'm': [((0,2,2,0,0,0),(1,3,4,1,1,1),'E'),
          ((None,0,2,2,1,0),(0,1,3,4,2,1),'A')],
    '7': [((0,2,0,1,0,0),(1,3,1,2,1,1),'E'),
          ((None,0,2,0,2,0),(0,1,3,1,4,1),'A')],
    'm7': [((0,2,0,0,0,0),(1,3,1,1,1,1),'E'),
           ((None,0,2,0,1,0),(0,1,3,1,2,1),'A')],
}

def lookup(chord):
    """Return one playable voicing; never replace unsupported chord qualities."""
    match = re.fullmatch(r'([A-G](?:#|b)?)(m7|maj7|sus2|sus4|add9|m|7)?(?:/([A-G](?:#|b)?))?', chord)
    if not match:
        return None
    root = FLATS.get(match[1],match[1]); quality = match[2] or ''
    if root not in NOTES: return None
    bass = FLATS.get(match[3],match[3]) if match[3] else None
    key = root + quality + ('/'+bass if bass else '')
    if key in OPEN: return Voicing(*OPEN[key])
    if bass: return None
    candidates = []
    for frets,fingers,base in MOVABLE.get(quality,[]):
        shift = (NOTES.index(root)-NOTES.index(base)) % 12
        if shift == 0: continue  # open forms are explicitly curated above
        candidates.append((shift,Voicing(tuple(None if f is None else f+shift for f in frets),fingers)))
    return min(candidates,key=lambda item:item[0])[1] if candidates else None
