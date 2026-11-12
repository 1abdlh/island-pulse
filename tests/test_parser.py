"""Run: DISABLE_POLL=1 python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from pathlib import Path

os.environ['DISABLE_POLL'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import SLUGS, parse_creator, numeric, dashboard

TEST_HTML = """<!doctype html><body>
<h1>lolbrotherz</h1><button>Follow</button>
<div>8 <span>#1,833</span> Players right now</div>
<div>161.2K <span>#19,906</span> Minutes Played</div>
<div>25 <span>#27,188</span> Followers</div>
<div>197 <span>#34,351</span> Favorites</div>
<div>Maps Charts Update history Sort By</div>
<table><tr><td><a href="/island/1446-2126-4835">jump drive and fly parkour</a></td>
<td>Players Now 5</td><td>All-Time Peak 277</td><td>Minutes Played 28.7K</td>
<td>24h Plays 556</td><td>24h Favorites 14</td><td>24h Recommends 0</td>
<td>24h Players 496</td><td>24h Avg Playtime 21</td><td>24h Retention 0%</td></tr>
<tr><td><a href="https://fortnite.gg/island/3048-0390-9099">PARKOUR WORLD</a></td>
<td>Players Now 3</td><td>All-Time Peak 411</td><td>Minutes Played 132.5K</td>
<td>24h Plays 258</td><td>24h Favorites 9</td><td>24h Recs 2</td>
<td>24h Players 210</td><td>24h Avg Playtime 21</td><td>24h Retention 16%</td></tr></table>
"""

class TestScraping(unittest.TestCase):
    def test_reference_creators(self):
        value = dashboard()
        self.assertEqual(value['tracked'], 10)
        self.assertEqual(value['totals']['ccu'], 114)
        self.assertEqual(value['totals']['minutes_played'], 910776)
        self.assertCountEqual(SLUGS, (x['slug'] for x in value['creators']))

    def test_abbreviations(self):
        self.assertEqual(numeric('1.2M'), 1200000)
        self.assertEqual(numeric('25.8K'), 25800)
        self.assertEqual(numeric('1,049'), 1049)
        self.assertIsNone(numeric('unknown'))

    def test_profile_parsing(self):
        profile = parse_creator(TEST_HTML, 'lolbrotherz')
        self.assertEqual(profile['ccu'], 8)
        self.assertEqual(profile['minutes_played'], 161200)
        self.assertEqual(profile['followers'], 25)
        self.assertEqual(profile['favorites'], 197)
        self.assertEqual(profile['map_count'], 2)
        self.assertEqual(profile['plays_24h'], 814)
        self.assertEqual(profile['players_24h'], 706)
        self.assertEqual(profile['recommends_24h'], 2)
        self.assertEqual(profile['maps'][0]['all_time_peak'], 277)

    def test_unavailable_profile_not_zero(self):
        with self.assertRaises(ValueError):
            parse_creator('<h1>lolbrotherz</h1><p>Access denied</p>', 'lolbrotherz')

if __name__ == '__main__':
    unittest.main()
