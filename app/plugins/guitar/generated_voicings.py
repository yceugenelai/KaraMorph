"""Generate conservative standard-tuning shapes and persist reusable results."""
import itertools
import json
import re
from pathlib import Path

from app.plugins.guitar.voicings import FLATS, NOTES, Voicing, lookup
from app.storage import atomic_json

TUNING = (4, 9, 2, 7, 11, 4)
QUALITIES = {
    '': (0,4,7), 'm': (0,3,7), 'dim': (0,3,6), 'dim7': (0,3,6,9),
    'aug': (0,4,8), '7': (0,4,7,10), 'maj7': (0,4,7,11),
    'm7': (0,3,7,10), 'm7b5': (0,3,6,10), '6': (0,4,7,9),
    'm6': (0,3,7,9), 'sus2': (0,2,7), 'sus4': (0,5,7),
    '7sus4': (0,5,7,10), 'add9': (0,2,4,7), '9': (0,2,4,7,10),
    'maj9': (0,2,4,7,11), 'm9': (0,2,3,7,10),
    '11': (0,2,4,5,7,10), '13': (0,2,4,7,9,10),
}
PATTERN = re.compile(r'^([A-G](?:#|b)?)(sus4\(b7\)|m7b5|7sus4|min9|min7|maj9|maj7|hdim7|dim7|add9|sus2|sus4|dim|aug|min|maj|m9|m7|m6|m|13|11|7|9|6)?(?:/([A-G](?:#|b)?|b?[1-7]))?$')
ALIASES = {'maj':'', 'min':'m', 'min7':'m7', 'min9':'m9', 'hdim7':'m7b5', 'sus4(b7)':'7sus4'}
DEGREES = {'1':0, 'b2':1, '2':2, 'b3':3, '3':4, '4':5, 'b5':6, '5':7,
           'b6':8, '6':9, 'b7':10, '7':11}

def parse_chord(chord):
    match = PATTERN.fullmatch(chord)
    if not match: return None
    root = FLATS.get(match[1], match[1])
    if root not in NOTES: return None
    root_pc = NOTES.index(root)
    bass_text = match[3]
    bass = (root_pc + DEGREES[bass_text]) % 12 if bass_text in DEGREES else (
        NOTES.index(FLATS.get(bass_text,bass_text)) if bass_text else root_pc)
    intervals = QUALITIES[ALIASES.get(match[2],match[2] or '')]
    pitches = {(root_pc + n) % 12 for n in intervals} | {bass}
    # A perfect fifth can be omitted; alterations, sevenths and extensions cannot.
    required = {(root_pc + n) % 12 for n in intervals if n != 7} | {bass}
    return pitches, required, bass

def generate(chord):
    parsed = parse_chord(chord)
    if not parsed: return None
    pitches, required, bass = parsed
    best = None
    for start in range(1, 13):
        choices = [[None] + ([0] if string % 12 in pitches else []) +
                   [f for f in range(start, min(start+4, 13)) if (string+f) % 12 in pitches]
                   for string in TUNING]
        for frets in itertools.product(*choices):
            sounding = [(i,f) for i,f in enumerate(frets) if f is not None]
            if len(sounding) < 3 or (TUNING[sounding[0][0]] + sounding[0][1]) % 12 != bass:
                continue
            found = {(TUNING[i]+f) % 12 for i,f in sounding}
            if not required <= found: continue
            used = sorted({f for _,f in sounding if f > 0})
            if len(used) > 4 or (used and used[-1]-used[0] > 3): continue
            groups = {f:[i for i,x in sounding if x == f] for f in used}
            # Barre may pass over higher frets, but cannot mute an intervening open/muted string.
            if any(any(frets[i] is None or frets[i] < f for i in range(group[0],group[-1]+1))
                   for f,group in groups.items() if len(group)>1):
                continue
            fingers = tuple(0 if f in (None,0) else used.index(f)+1 for f in frets)
            # Prefer complete chords, lower positions and fewer fingers/mutes.
            score = (len(pitches-found), max(used,default=0), len(used), 6-len(sounding), sum(used))
            if best is None or score < best[0]: best = (score,Voicing(tuple(frets),fingers))
    return best[1] if best else None

class VoicingCache:
    def __init__(self,path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.entries = data.get('entries',{}) if data.get('version') == 1 else {}
            if not isinstance(self.entries,dict): self.entries = {}
        except (OSError,ValueError,TypeError,AttributeError):
            self.entries = {}
        self.memory = {}
        self.save_error = None

    def get(self,chord):
        predefined = lookup(chord)
        if predefined: return predefined
        if chord in self.memory: return self.memory[chord]
        entry = self.entries.get(chord)
        voicing = None
        if isinstance(entry,dict):
            try:
                candidate = Voicing(tuple(entry['frets']),tuple(entry['fingers']))
                # Revalidate cached values rather than trusting a manually edited file.
                parsed = parse_chord(chord)
                if parsed and valid(candidate,*parsed): voicing = candidate
            except (KeyError,TypeError,ValueError): pass
        if voicing is None:
            voicing = generate(chord)
            if voicing:
                self.entries[chord] = {'frets':list(voicing.frets),'fingers':list(voicing.fingers)}
                try:
                    atomic_json(self.path,{'version':1,'entries':self.entries})
                    self.save_error = None
                except OSError as error: self.save_error = str(error)
        self.memory[chord] = voicing
        return voicing

def valid(voicing,pitches,required,bass):
    if len(voicing.frets)!=6 or len(voicing.fingers)!=6: return False
    if any(f is not None and (type(f) is not int or not 0<=f<=12) for f in voicing.frets): return False
    sounding=[(i,f) for i,f in enumerate(voicing.frets) if f is not None]
    if len(sounding)<3 or (TUNING[sounding[0][0]]+sounding[0][1])%12 != bass: return False
    found={(TUNING[i]+f)%12 for i,f in sounding}
    if not required <= found <= pitches: return False
    used=sorted({f for _,f in sounding if f>0})
    if len(used)>4 or (used and used[-1]-used[0]>3): return False
    if voicing.fingers != tuple(0 if f in (None,0) else used.index(f)+1 for f in voicing.frets): return False
    for fret in used:
        group=[i for i,x in sounding if x==fret]
        if any(voicing.frets[i] is None or voicing.frets[i]<fret for i in range(group[0],group[-1]+1)): return False
    return True
