import unittest
import re
import json
import tempfile
from pathlib import Path
from app.plugins.guitar.voicings import FLATS, NOTES, OPEN, lookup
from app.plugins.guitar.generated_voicings import VoicingCache, generate, parse_chord, valid

class GuitarVoicingTests(unittest.TestCase):
    def test_curated_and_transposable_shapes_match_chord_notes(self):
        intervals={'':{0,4,7},'m':{0,3,7},'7':{0,4,7,10},'m7':{0,3,7,10},
                   'maj7':{0,4,7,11},'sus2':{0,2,7},'sus4':{0,5,7},'add9':{0,2,4,7}}
        tuning=[4,9,2,7,11,4]
        for quality in ('','m','7','m7'):
            for root in NOTES:
                chord=root+quality
                voicing=lookup(chord)
                self.assertIsNotNone(voicing,chord)
                pitches={(tuning[i]+f-NOTES.index(root))%12 for i,f in enumerate(voicing.frets) if f is not None}
                self.assertTrue(intervals[quality]-{7} <= pitches <= intervals[quality],chord)
        for chord in OPEN:
            voicing=lookup(chord)
            match=re.fullmatch(r'([A-G](?:#|b)?)(m7|maj7|sus2|sus4|add9|m|7)?(?:/([A-G](?:#|b)?))?',chord)
            root=FLATS.get(match[1],match[1]); quality=match[2] or ''
            pitches={(tuning[i]+f-NOTES.index(root))%12 for i,f in enumerate(voicing.frets) if f is not None}
            allowed=intervals[quality] | ({(NOTES.index(FLATS.get(match[3],match[3]))-NOTES.index(root))%12} if match[3] else set())
            self.assertTrue(intervals[quality]-{7} <= pitches <= allowed,chord)
            if match[3]:
                lowest=next((tuning[i]+f)%12 for i,f in enumerate(voicing.frets) if f is not None)
                self.assertEqual(lowest,NOTES.index(FLATS.get(match[3],match[3])),chord)
            self.assertEqual(len(voicing.frets),6,chord)
            self.assertEqual(len(voicing.fingers),6,chord)
            for fret,finger in zip(voicing.frets,voicing.fingers):
                self.assertTrue((fret in (None,0) and finger == 0) or (fret and 1 <= finger <= 4),chord)

    def test_unsupported_chords_are_not_misrepresented(self):
        for chord in ('N','F#/A#','Cdim','—',''):
            self.assertIsNone(lookup(chord),chord)
        self.assertEqual(lookup('Bb'),lookup('A#'))

    def test_generated_shapes_match_notes_bass_and_constraints(self):
        for chord in ('Cdim','F#/A#','Gmaj9','Cdim7','Cm7b5','C9','C7sus4',
                      'Bmaj/3','Bmaj/2','Cmin/b3','Chdim7','Csus4(b7)'):
            result=generate(chord)
            self.assertIsNotNone(result,chord)
            self.assertTrue(valid(result,*parse_chord(chord)),chord)
        self.assertIsNone(generate('Cunknown'))
        self.assertIsNone(generate('N'))

    def test_generated_voicings_are_saved_reused_and_invalid_cache_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'voicings.json'
            cache=VoicingCache(path)
            first=cache.get('Cdim')
            self.assertIsNotNone(first)
            self.assertIn('Cdim',json.loads(path.read_text(encoding='utf-8'))['entries'])
            self.assertEqual(VoicingCache(path).get('Cdim'),first)
            path.write_text(json.dumps({'version':1,'entries':{'Cdim':{'frets':[0]*6,'fingers':[0]*6}}}))
            self.assertEqual(VoicingCache(path).get('Cdim'),first)

if __name__=='__main__': unittest.main()
