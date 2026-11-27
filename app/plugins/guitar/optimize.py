"""Beat aggregation, soft key preference, Viterbi and guitar-friendly views."""
import numpy as np
NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

def segments(labels, choices, bounds, scores=None):
    out = []
    for i, choice in enumerate(choices):
        chord = labels[int(choice)]
        if out and out[-1]['chord'] == chord:
            out[-1]['end'] = float(bounds[i+1])
        else:
            out.append({'start': float(bounds[i]), 'end': float(bounds[i+1]), 'chord': chord})
    return out

def key_estimate(labels, scores):
    major = np.array([1,0,1,0,1,1,0,1,0,1,0,1])
    minor = np.array([1,0,1,1,0,1,0,1,1,0,1,0])
    pitch = np.zeros(12)
    for i, label in enumerate(labels[:-1]):
        root = label[:2] if len(label)>1 and label[1]=='#' else label[:1]
        pitch[NOTES.index(root)] += max(0., float(scores[i].mean()))
    ranking = [(float(pitch @ np.roll(scale, root)), root, kind)
               for root in range(12) for scale, kind in ((major,'major'), (minor,'minor'))]
    _, root, kind = max(ranking)
    return root, kind, major if kind == 'major' else minor

def analyze(data, strength=1., beat_step=2):
    labels, scores, times = data['labels'], data['scores'], data['times']
    duration = data['duration']
    raw_bounds = np.r_[times, duration]
    raw = data.get('raw_segments') or segments(labels, np.argmax(scores, axis=0), raw_bounds)
    beats = np.asarray(data['beats'])
    # Half-beat candidates permit strong off-beat changes; 2-beat preference is a transition cost.
    if len(beats) >= 2:
        half = np.sort(np.r_[beats, (beats[:-1]+beats[1:])/2])
        bounds = np.unique(np.r_[0., half[(half > .05) & (half < duration-.05)], duration])
    else:
        bounds = np.arange(0, duration, .5); bounds = np.r_[bounds, duration]
    agg = np.zeros((len(labels), len(bounds)-1))
    for i, (a,b) in enumerate(zip(bounds[:-1], bounds[1:])):
        indices = np.flatnonzero((times >= a) & (times < b))
        if not len(indices): indices = [min(len(times)-1, int(np.searchsorted(times, a)))]
        agg[:,i] = scores[:,indices].mean(axis=1)
    has_key = bool(len(labels) > 1 and np.mean(np.argmax(scores, axis=0) != len(labels)-1) >= .1)
    root, kind, scale = key_estimate(labels, scores) if has_key else (0, 'unknown', np.zeros(12))
    bonus = np.zeros(len(labels))
    for i, label in enumerate(labels[:-1]):
        note = label[:2] if len(label)>1 and label[1]=='#' else label[:1]
        if has_key: bonus[i] = .025 if np.roll(scale, root)[NOTES.index(note)] else -.025
    emission = agg + bonus[:,None]
    count, n = emission.shape
    dp, back = np.zeros((count,n)), np.zeros((count,n), dtype=int)
    dp[:,0] = emission[:,0]
    for i in range(1,n):
        # Stronger evidence can overcome either penalty, including at half-beats.
        grid = np.min(np.abs(beats-bounds[i])) if len(beats) else 0
        penalty = strength * (.14 + (.13 if grid > .06 else 0))
        if beat_step == 2 and len(beats) and np.argmin(np.abs(beats-bounds[i])) % 2: penalty += strength*.05
        candidates = dp[:,i-1,None] - penalty * (1-np.eye(count))
        # Enter or exit N readily, avoiding forced chords in silence.
        candidates[-1,:-1] += penalty*.6; candidates[:-1,-1] += penalty*.6
        back[:,i] = np.argmax(candidates, axis=0)
        dp[:,i] = emission[:,i] + candidates[back[:,i], np.arange(count)]
    choices = np.empty(n,dtype=int); choices[-1] = np.argmax(dp[:,-1])
    for i in range(n-1,0,-1): choices[i-1] = back[choices[i],i]
    smooth = segments(labels, choices, bounds)
    simple = []
    for s in smooth:
        c = s['chord']
        # Preserve dominant sevenths; collapse major/minor sevenths and sus ornaments.
        for suffix, replacement in [('maj7',''), ('m7','m'), ('sus2',''), ('sus4','')]:
            if c.endswith(suffix): c = c[:-len(suffix)] + replacement; break
        if simple and simple[-1]['chord'] == c: simple[-1]['end'] = s['end']
        else: simple.append({**s, 'chord': c})
    return {'raw': raw, 'raw_model':data.get('raw_model'), 'smooth': smooth, 'simple': simple, 'standard': smooth,
            'key': f'{NOTES[root]} {kind}' if has_key else 'unknown', 'capo': capo(simple),
            'score_note': ('C1 model does not expose scores; postprocessing aggregates label votes, not confidence.'
                           if data.get('engine') == 'C1' else 'C0 template similarities; not calibrated confidence'),
            'model': ('LV-Chordia 1.1.0' if data.get('engine') == 'C1' else 'librosa 0.11.0 chroma_stft templates'),
            'duration': duration,
            'tempo': data['tempo'], 'timing': data['timing']}

def capo(chords):
    easy = {'C':0,'D':0,'E':0,'G':0,'A':0,'Am':0,'Dm':1,'Em':0,
            'A7':1,'D7':1,'E7':1,'G7':1,'C7':2,'F':3,'Bm':3}
    ranking=[]
    for fret in range(8):
        total = 0.; shapes = {}
        for seg in chords:
            c=seg['chord']
            if c == 'N': continue
            r=c[:2] if len(c)>1 and c[1]=='#' else c[:1]
            shape=NOTES[(NOTES.index(r)-fret)%12]+c[len(r):]
            total += (seg['end']-seg['start'])*easy.get(shape, 4)
            shapes[c]=shape
        ranking.append({'fret':fret,'difficulty':round(total,2),'shapes':shapes})
    return sorted(ranking,key=lambda x:(x['difficulty'],x['fret']))
