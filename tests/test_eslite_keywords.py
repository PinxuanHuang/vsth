import tempfile
import unittest
from pathlib import Path

from config import BOOKING_SITES, Settings, load_settings, parse_eslite_movie_keywords, save_settings
from eslite import movie_link
from eslite_tickets import ticket_name


BASE = 'https://arthouse.eslite.com/visSelect.aspx?visSearchBy=cin&visCinID=dynamic'


def snapshot(*titles):
    return dict(url=BASE, cinemas=[dict(text='影院', href=BASE, disabled=False)],
                movies=[dict(text=title, href=BASE + '&visMovieName=movie-' + str(i), disabled=False)
                        for i, title in enumerate(titles)])


class EsliteKeywordTests(unittest.TestCase):
    def test_any_keyword_matches_and_same_item_is_counted_once(self):
        words = '辣妹過招,秘密會議,特別場,套票場'
        for title in ('辣妹過招 Mean Girls', '秘密會議 Conclave', '週末特別場', '粉紅套票場',
                      '辣妹過招＋秘密會議 特別場 套票場'):
            with self.subTest(title=title):
                data = snapshot('其他電影', title)
                self.assertEqual(movie_link(data, words), data['movies'][1])

    def test_fullwidth_commas_spaces_empty_segments_and_duplicates(self):
        words = ' 辣妹過招 ， 秘密會議,, 特別場,辣妹過招, '
        self.assertEqual(parse_eslite_movie_keywords(words), ['辣妹過招', '秘密會議', '特別場'])
        self.assertEqual(parse_eslite_movie_keywords(' ＡＢＣ,abc '), ['abc'])
        self.assertIsNotNone(movie_link(snapshot('秘密會議 Conclave'), words))

    def test_multiple_matching_movies_pause_without_keyword_order_priority(self):
        data = snapshot('辣妹過招', '秘密會議 特別場')
        for words in ('辣妹過招,秘密會議', '秘密會議,辣妹過招'):
            with self.assertRaisesRegex(ValueError, '多個電影項目'):
                movie_link(data, words)

    def test_single_keyword_preserves_exact_match_preference(self):
        data = snapshot('秘密會議', '秘密會議 特別場')
        self.assertEqual(movie_link(data, '秘密會議'), data['movies'][0])

    def test_missing_disabled_external_or_other_cinema_is_not_selected(self):
        self.assertIsNone(movie_link(snapshot('其他電影'), '辣妹過招,秘密會議'))
        for change in (dict(disabled=True), dict(href=BASE.replace('dynamic', 'other') + '&visMovieName=other'),
                       dict(href=BASE.replace('arthouse.eslite.com', 'example.com') + '&visMovieName=other')):
            data = snapshot('秘密會議')
            data['movies'][0].update(change)
            self.assertIsNone(movie_link(data, '辣妹過招,秘密會議'))

    def test_blank_waits_for_manual_selection_but_only_commas_are_invalid(self):
        self.assertEqual(parse_eslite_movie_keywords('  '), [])
        self.assertIsNone(movie_link(snapshot('秘密會議'), ''))
        for text in (',,', ' ，, '):
            with self.assertRaises(ValueError):
                Settings(url=BOOKING_SITES['誠品'], showtime='2026-10-03', eslite_movie=text).validate()
        Settings(cinema='test', eslite_movie=',,').validate()

    def test_multi_keyword_settings_roundtrip(self):
        settings = Settings(url=BOOKING_SITES['誠品'], showtime='2026-10-03',
                            eslite_movie='辣妹過招,秘密會議,特別場,套票場')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)

    def test_ticket_matching_remains_normalized_exact_not_contains(self):
        self.assertEqual(ticket_name(dict(label='全票310:', price='310')), '全票')
        self.assertEqual(ticket_name(dict(label='單人套票450:', price='450')), '單人套票')
        self.assertNotEqual(ticket_name(dict(label='特別場單人套票450:', price='450')), '單人套票')
        self.assertNotEqual(ticket_name(dict(label='單人套票450:', price='450')), '個人套票')


if __name__ == '__main__':
    unittest.main()
