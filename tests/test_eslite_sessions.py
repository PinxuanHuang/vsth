import unittest
from datetime import date

from config import BOOKING_SITES, Settings, ESLITE_CINEMAS
from eslite import cinema_link, movie_link, session_link


BASE = 'https://arthouse.eslite.com/visSelect.aspx?visSearchBy=cin&visCinID=opaque'


def link(time, session, disabled=False):
    return dict(text=time + ' B廳', title=time, disabled=disabled,
                href='https://arthouse.eslite.com/visSelectTickets.aspx?cinemacode=opaque&txtSessionId=' + session)


def snapshot():
    return dict(url=BASE + '&visMovieName=encoded', tables=[[
        dict(text='10月3日 星期日', links=[]),
        dict(text='', links=[link('20:00', 'first'), link('09:00', 'middle'), link('16:00', 'last')]),
        dict(text='10月4日 星期一', links=[]),
        dict(text='', links=[link('22:00', 'other-day')])]])


class EsliteSessionRulesTests(unittest.TestCase):
    def test_first_last_use_dom_order_not_chronological_order(self):
        for position, expected in (('first', 'first'), ('last', 'last')):
            result = session_link(snapshot(), '2032-10-03', session_position=position)
            self.assertTrue(result['href'].endswith('=' + expected))

    def test_explicit_time_overrides_position_and_missing_time_never_falls_back(self):
        for position in ('first', 'last'):
            result = session_link(snapshot(), '2032-10-03', '09:00', session_position=position)
            self.assertTrue(result['href'].endswith('=middle'))
            self.assertIsNone(session_link(snapshot(), '2032-10-03', '22:00', session_position=position))

    def test_wrong_date_and_explicit_year_never_match(self):
        data = snapshot()
        self.assertIsNone(session_link(data, '2032-10-05'))
        data['tables'][0][0]['text'] = '2031年10月3日'
        self.assertIsNone(session_link(data, '2032-10-03'))

    def test_requested_year_used_for_yearless_date_not_next_occurrence(self):
        result = session_link(snapshot(), '2032-10-03', today=date(2032, 12, 31))
        self.assertIsNotNone(result)

    def test_disabled_first_or_last_not_skipped(self):
        for position, index in (('first', 0), ('last', -1)):
            data = snapshot()
            data['tables'][0][1]['links'][index]['disabled'] = True
            self.assertIsNone(session_link(data, '2032-10-03', session_position=position))

    def test_invalid_endpoint_never_uses_another_session(self):
        for replacement in ('https://example.com/', 'https://arthouse.eslite.com/visSelectTickets.aspx?cinemacode=other&txtSessionId=x'):
            data = snapshot()
            data['tables'][0][1]['links'][0]['href'] = replacement
            self.assertIsNone(session_link(data, '2032-10-03'))

    def test_duplicate_date_or_time_is_ambiguous(self):
        data = snapshot()
        data['tables'][0][2]['text'] = '10月3日'
        with self.assertRaises(ValueError):
            session_link(data, '2032-10-03')
        data = snapshot()
        data['tables'][0][1]['links'].append(link('09:00', 'duplicate'))
        with self.assertRaises(ValueError):
            session_link(data, '2032-10-03', '09:00')

    def test_cinema_matches_settings_not_first_link_and_reads_actual_code(self):
        data = dict(cinemas=[dict(text='其他影院', href=BASE.replace('opaque', 'other'), disabled=False),
                             dict(text=ESLITE_CINEMAS[0] + ' Eslite Songyan', href=BASE, disabled=False)])
        self.assertEqual(cinema_link(data, ESLITE_CINEMAS[0])['href'], BASE)
        self.assertIsNone(cinema_link(data, '不存在的影院'))

    def test_blank_movie_waits_for_user_then_uses_current_selection(self):
        cinema = dict(text='影院', href=BASE, disabled=False)
        selected = dict(text='手動選擇的片名', href=BASE + '&visMovieName=encoded', disabled=False)
        other = dict(text='其他電影', href=BASE + '&visMovieName=other', disabled=False)
        data = dict(url=BASE, cinemas=[cinema], movies=[other, selected])
        self.assertIsNone(movie_link(data, ''))
        data['url'] = selected['href']
        self.assertEqual(movie_link(data, ''), selected)

    def test_settings_require_date_and_cinema_but_time_is_optional(self):
        for position in ('first', 'last'):
            Settings(url=BOOKING_SITES['誠品'], showtime='2032-10-03', session_position=position).validate()
        for changes in (dict(showtime=''), dict(eslite_cinema='威秀'), dict(session_position='invalid'), dict(eslite_time='25:00')):
            data = dict(url=BOOKING_SITES['誠品'], showtime='2032-10-03')
            data.update(changes)
            with self.assertRaises(ValueError):
                Settings(**data).validate()


if __name__ == '__main__':
    unittest.main()
