import json
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from automation import Cancelled, TicketFlow, resource, select_seats_and_continue
from config import Settings, load_settings, parse_preferred_seats, save_settings
from seating import READ_SEATS, plan_seats, seat_label


class SeatConfigTests(unittest.TestCase):
    def test_old_settings_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'url': 'demo://ticket', 'cinema': 'test'}))
            self.assertEqual(load_settings(path).seat_mode, 'manual')
            self.assertEqual(load_settings(path).seat_preferred, '')
            settings = Settings(url='demo://seats', cinema='test', seat_mode='custom',
                                seat_row_start=40, seat_row_end=90, seat_col_start=20,
                                seat_col_end=80, seat_direction='right', seat_contiguous=False,
                                seat_preferred='N7,N8,N9,M7,M8,M9')
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)

    def test_invalid_ranges_and_modes(self):
        for kwargs in ({'seat_row_start': 100}, {'seat_col_end': -1}, {'seat_row_start': True},
                       {'seat_row_end': 3.5}, {'seat_mode': 'unknown'}, {'seat_contiguous': 'yes'},
                       {'seat_direction': 'up'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs).validate_seats()

    def test_preferred_seats_normalize_and_deduplicate_in_input_order(self):
        self.assertEqual(parse_preferred_seats(' n7, N-08，N7, m09, AA12, '), ['N7', 'N8', 'M9', 'AA12'])
        self.assertEqual(parse_preferred_seats(' , ， '), [])

    def test_invalid_preferred_seats(self):
        for value in (None, ['N7'], 7, 'N', '7', 'N0', 'N-1-N3', 'N7 M8', 'N7;M8'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '優先座位'):
                Settings(seat_preferred=value).validate_seats()


class SeatBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(resource('seats_fixture.html').as_uri())
        self.settings = Settings(seat_mode='middle', tickets=3)
        self.stop = threading.Event()
        self.logs = []

    def tearDown(self):
        self.context.close()

    def read(self):
        return self.page.locator('#divSeatMap .Seating-Area').evaluate_all(READ_SEATS)

    def run_selection(self):
        select_seats_and_continue(self.page, self.settings, self.logs.append, self.stop, timeout=.4)

    def test_sample_coordinates_preserve_gaps_labels_and_status(self):
        self.page.route('**/img/*', lambda route: route.abort())
        self.page.set_content((Path(__file__).parent / 'seat_map_fixture.html').read_text(encoding='utf-8'))
        seats = self.read()
        self.assertEqual(len(seats), 281)
        seat = next(s for s in seats if s['row'] == 'E' and s['label'] == '7')
        self.assertEqual((seat['gridRow'], seat['gridCol']), (5, 10))
        self.assertEqual((seat['backendRow'], seat['backendSeat']), ('11', '20'))
        self.assertEqual(sum(s['available'] for s in seats), 269)
        self.assertEqual(sum(s['selected'] for s in seats), 1)
        self.assertEqual(len(plan_seats(seats, self.settings)), 3)
        self.page.locator('#E-18').evaluate("el => {el.dataset.status='0';el.querySelector('img').src='../img/standard_available.png'}")
        for mode in ('front', 'middle', 'back'):
            self.settings.seat_mode = mode
            plan = plan_seats(self.read(), self.settings)
            self.assertEqual(len(plan), 3)
            self.assertEqual(len({s['gridRow'] for s in plan}), 1)
            self.assertEqual(max(s['gridCol'] for s in plan) - min(s['gridCol'] for s in plan), 2)

    def test_automatic_select_and_continue(self):
        expected = plan_seats(self.read(), self.settings)
        self.run_selection()
        result = self.page.locator('#result').inner_text()
        for seat in expected:
            self.assertIn(f"{seat['row']}-{seat['label']}", result)
        self.assertEqual(self.page.locator('#btnCheckOut').count(), 0)
        self.assertFalse(self.page.is_closed())

    def test_preferred_seats_override_region_and_follow_input_order(self):
        self.settings.tickets = 2
        self.settings.seat_preferred = 'b3,B2,B1,K7,K8'
        self.assertEqual([seat_label(s) for s in plan_seats(self.read(), self.settings)], ['B3', 'B2'])
        self.run_selection()
        self.assertEqual(self.page.locator('#result').inner_text(), '測試完成：B-2、B-3')
        self.assertTrue(any('優先使用指定座位：B3、B2' in line for line in self.logs))

    def test_partial_preference_fills_adjacent_seats(self):
        self.settings.seat_preferred = 'B1'
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual({seat_label(s) for s in plan}, {'B1', 'B2', 'B3'})
        self.run_selection()
        self.assertEqual(self.page.locator('#result').inner_text(), '測試完成：B-1、B-2、B-3')

    def test_preferred_seats_never_bridge_an_aisle(self):
        self.settings.seat_preferred = 'B3,B4,B5'
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual({seat_label(s) for s in plan}, {'B2', 'B3', 'B4'})

    def test_isolated_preferred_seat_falls_back_without_partial_clicks(self):
        baseline = plan_seats(self.read(), self.settings)
        self.page.locator('#B-2').evaluate("el => {el.dataset.type='Sold';el.dataset.status='3'}")
        self.settings.seat_preferred = 'B1'
        self.assertEqual(plan_seats(self.read(), self.settings), baseline)
        self.run_selection()
        self.assertNotIn('B-1', self.page.locator('#result').inner_text())
        self.assertTrue(any('改用原本選位規則' in line for line in self.logs))

    def test_noncontiguous_preferences_fill_from_original_region(self):
        self.settings.seat_contiguous = False
        baseline = plan_seats(self.read(), self.settings)
        self.settings.seat_preferred = 'B1,K18,B1'
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual([seat_label(s) for s in plan[:2]], ['B1', 'K18'])
        self.assertEqual(plan[2], baseline[0])

    def test_all_unavailable_or_absent_preferences_preserve_original_plan(self):
        baseline = plan_seats(self.read(), self.settings)
        # E9 is sold in the local fixture; N7 and Z99 do not exist.
        self.settings.seat_preferred = 'E9,N7,Z99'
        self.assertEqual(plan_seats(self.read(), self.settings), baseline)

    def test_preferred_seats_do_not_mix_areas(self):
        self.page.locator('.Seating-Area').evaluate('el => el.after(el.cloneNode(true))')
        self.page.locator('.Seating-Area').nth(0).locator('#B-2').evaluate("el => {el.dataset.type='Sold';el.dataset.status='3'}")
        self.page.locator('.Seating-Area').nth(1).locator('#B-1').evaluate("el => {el.dataset.type='Sold';el.dataset.status='3'}")
        self.settings.seat_contiguous = False
        self.settings.seat_preferred = 'B1,B2'
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual({s['area'] for s in plan}, {0})
        self.assertEqual(seat_label(plan[0]), 'B1')
        self.assertNotIn('B2', [seat_label(s) for s in plan])

    def test_new_hall_sample_matches_display_labels_and_sold_status(self):
        self.page.route('**/img/*', lambda route: route.abort())
        self.page.set_content((Path(__file__).parent / 'preferred_seat_map_fixture.html').read_text(encoding='utf-8'))
        self.settings.seat_mode = 'front'
        baseline = plan_seats(self.read(), self.settings)
        self.settings.seat_preferred = 'N7,N8,N9,M7,M8,M9'
        # These six seats are sold in the supplied fragment.
        self.assertEqual(plan_seats(self.read(), self.settings), baseline)
        self.page.locator('#M-7, #M-8, #M-9').evaluate_all("""els => els.forEach(el => {
            el.dataset.type='Empty';el.dataset.status='0';
            el.querySelector('img').src='../img/standard_available.png';
        })""")
        self.assertEqual([seat_label(s) for s in plan_seats(self.read(), self.settings)], ['M7', 'M8', 'M9'])
        self.page.locator('#N-7, #N-8, #N-9').evaluate_all("""els => els.forEach(el => {
            el.dataset.type='Empty';el.dataset.status='0';
            el.querySelector('img').src='../img/standard_available.png';
        })""")
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual([seat_label(s) for s in plan], ['N7', 'N8', 'N9'])
        self.assertEqual([(s['backendRow'], s['backendSeat']) for s in plan], [('1', '15'), ('1', '14'), ('1', '13')])

    def install_site_handler(self, selected_ids):
        self.settings.tickets = 2
        self.page.route('**/img/*', lambda route: route.abort())
        self.page.evaluate("""ids => {
            document.querySelectorAll('td[data-type="Sold"]').forEach(el => {
                el.dataset.type='Empty'; el.dataset.status='0';
            });
            document.querySelectorAll('td[data-name]').forEach(el => {
                el.onclick=null;
                const selected=ids.includes(el.id);
                el.dataset.status=selected?'5':'0';
                const img=document.createElement('img');
                img.src=selected?'../img/standard_selected.png':'../img/standard_available.png';
                img.style.width='20px'; img.style.height='20px';
                el.append(img);
            });
            const button=document.querySelector('#btnCheckOut');
            button.classList.add('disabled');
            button.onclick=()=>{
                window.submitted=SelectSeats.map(id=>{
                    const el=document.getElementById(id);
                    return {id, row:el.dataset.row, seat:el.dataset.seatnum};
                });
                document.querySelector('#select-seats-container').remove();
            };
        }""", selected_ids)
        self.page.add_script_tag(path=str(Path(__file__).parent / 'jquery.site.min.js'))
        self.page.add_script_tag(path=str(Path(__file__).parent / 'site_seat_handler.js'))
        self.page.wait_for_function('(n) => SelectSeats.length === n', arg=len(selected_ids))

    def test_real_site_click_does_not_cancel_preselection(self):
        self.install_site_handler(['B-1', 'B-2'])
        self.page.locator('#B-2').click()
        self.assertEqual(self.page.evaluate('SelectSeats'), ['B-1', 'B-2'])
        self.run_selection()
        self.assertTrue(all(s['id'] not in ('B-1', 'B-2') for s in self.page.evaluate('submitted')))

    def test_real_site_fifo_handles_evicted_overlapping_target(self):
        self.install_site_handler([])
        plan = plan_seats(self.read(), self.settings)
        first = plan[0]['id']
        self.page.locator(f'[id="{first}"]').click()
        self.page.locator('#B-1').click()
        self.run_selection()
        self.assertEqual({s['id'] for s in self.page.evaluate('submitted')}, {s['id'] for s in plan})
        self.assertEqual({(s['row'], s['seat']) for s in self.page.evaluate('submitted')},
                         {(s['backendRow'], s['backendSeat']) for s in plan})

    def test_real_site_fifo_preserves_preferred_selection(self):
        self.install_site_handler(['B-1', 'B-2'])
        self.settings.seat_preferred = 'B1,B3,B4'
        self.settings.seat_contiguous = False
        self.run_selection()
        self.assertEqual({s['id'] for s in self.page.evaluate('submitted')}, {'B-1', 'B-3'})

    def test_real_site_selected_targets_need_no_clicks(self):
        self.install_site_handler([])
        plan = plan_seats(self.read(), self.settings)
        for seat in plan:
            self.page.locator(f'[id="{seat["id"]}"]').click()
        self.page.evaluate("$('td').off('click')")
        self.run_selection()
        self.assertEqual({s['id'] for s in self.page.evaluate('submitted')}, {s['id'] for s in plan})

    def test_real_site_array_change_without_images_blocks_checkout(self):
        self.install_site_handler(['B-1', 'B-2'])
        self.page.evaluate("""() => {
            $('td').off('click').click(function () {
                SelectSeats.shift(); SelectSeats.push(this.id);
            });
        }""")
        with self.assertRaisesRegex(ValueError, '網站確認替換'):
            self.run_selection()
        self.assertIsNone(self.page.evaluate('window.submitted'))

    def test_real_site_disabled_class_blocks_checkout(self):
        self.install_site_handler([])
        self.page.evaluate("document.querySelector('table').addEventListener('click',()=>document.querySelector('#btnCheckOut').classList.add('disabled'))")
        with self.assertRaisesRegex(ValueError, '繼續按鈕'):
            self.run_selection()
        self.assertIsNone(self.page.evaluate('window.submitted'))

    def test_real_site_delayed_queue_initialization(self):
        self.install_site_handler(['B-1', 'B-2'])
        self.page.evaluate("SelectSeats=[]; setTimeout(()=>{SelectSeats=['B-1','B-2']}, 150)")
        self.run_selection()
        self.assertEqual(len(self.page.evaluate('submitted')), 2)

    def test_real_site_error_redirect_is_not_success(self):
        self.install_site_handler(['B-1', 'B-2'])
        self.page.route('https://sales.vscinemas.com.tw/**', lambda route: route.fulfill(body='<h1>Error</h1>'))
        self.page.locator('#btnCheckOut').evaluate("el => el.onclick=()=>location.href='https://sales.vscinemas.com.tw/VieShowTicketD4/Home/Error'")
        with self.assertRaisesRegex(ValueError, '錯誤頁'):
            self.run_selection()
        self.assertFalse(any('已完成選位並點擊繼續' in line for line in self.logs))

    def test_replaces_preselection_and_retains_matching_seat(self):
        plan = plan_seats(self.read(), self.settings)
        keep = f"{plan[0]['row']}-{plan[0]['label']}"
        self.page.locator(f'[id="{keep}"]').click()
        for seat_id in ('B-1', 'B-2'):
            self.page.locator(f'[id="{seat_id}"]').click()
        self.page.evaluate("""() => {
            window.clicked=[];
            document.querySelector('table').addEventListener('click', event => {
                window.clicked.push(event.target.closest('td').id);
            });
        }""")
        self.run_selection()
        self.assertNotIn(keep, self.page.evaluate('window.clicked'))
        self.assertEqual(self.page.evaluate('window.clicked.slice(0,2)'), ['B-1', 'B-2'])
        result = self.page.locator('#result').inner_text()
        for seat in plan:
            self.assertIn(f"{seat['row']}-{seat['label']}", result)
        self.assertNotIn('B-1', result)

    def test_unavailable_region_keeps_preselection(self):
        self.page.locator('#B-1').click()
        self.page.locator('#B-2').click()
        self.page.locator('td[data-status="0"]').evaluate_all("els => els.forEach(el => {el.dataset.type='Sold';el.dataset.status='3'})")
        with self.assertRaisesRegex(ValueError, '找不到'):
            self.run_selection()
        self.assertEqual({s['row'] + '-' + s['label'] for s in self.read() if s['selected']}, {'B-1', 'B-2'})

    def test_rejected_deselection_stops_before_new_selections(self):
        self.page.locator('#B-1').click()
        self.page.locator('#B-1').evaluate('el => el.onclick=()=>{}')
        with self.assertRaisesRegex(ValueError, '取消預選'):
            self.run_selection()
        self.assertEqual(sum(s['selected'] for s in self.read()), 1)
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_only_preselected_seats_are_valid_candidates(self):
        plan = plan_seats(self.read(), self.settings)
        for seat in plan:
            self.page.locator(f"[id='{seat['row']}-{seat['label']}']").click()
        self.page.locator('td[data-status="0"]').evaluate_all("els => els.forEach(el => {el.dataset.type='Sold';el.dataset.status='3'})")
        self.page.locator('td').evaluate_all("els => els.forEach(el => el.onclick=()=>{throw Error('must retain selected seats')})")
        self.run_selection()
        self.assertIn('測試完成', self.page.locator('#result').inner_text())

    def test_row_fallback_checks_all_rear_rows_before_front(self):
        self.settings.seat_mode = 'custom'
        self.settings.seat_row_start, self.settings.seat_row_end = 0, 100
        seats = self.read()
        anchor = plan_seats(seats, self.settings)[0]['gridRow']
        for contiguous in (True, False):
            self.settings.seat_contiguous = contiguous
            blocked = [dict(s, available=False) if s['gridRow'] in (anchor, anchor+1) else dict(s) for s in seats]
            self.assertEqual({s['gridRow'] for s in plan_seats(blocked, self.settings)}, {anchor+2})
            blocked = [dict(s, available=False) if s['gridRow'] >= anchor else dict(s) for s in seats]
            self.assertEqual({s['gridRow'] for s in plan_seats(blocked, self.settings)}, {anchor-1})

    def test_presets_search_rear_then_front_within_region(self):
        self.settings.tickets = 1
        for mode in ('front', 'middle', 'back'):
            self.settings.seat_mode = mode
            seats = self.read()
            anchor = plan_seats(seats, self.settings)[0]['gridRow']
            blocked = [dict(s, available=False) if s['gridRow'] == anchor else dict(s) for s in seats]
            self.assertGreater(plan_seats(blocked, self.settings)[0]['gridRow'], anchor)
            blocked = [dict(s, available=False) if s['gridRow'] >= anchor else dict(s) for s in seats]
            self.assertLess(plan_seats(blocked, self.settings)[0]['gridRow'], anchor)

    def test_supplied_dom_image_only_selection_confirmation(self):
        self.page.route('**/img/*', lambda route: route.abort())
        self.page.set_content((Path(__file__).parent / 'seat_map_fixture.html').read_text(encoding='utf-8'))
        # The pasted fragment omits the site's CSS and decorative image assets.
        self.page.locator('.Seating-Screen, .Seating-RowLabelContainer').evaluate_all('els => els.forEach(el => el.remove())')
        self.page.evaluate("""() => {
            document.querySelectorAll('td[data-name]').forEach(el => el.onclick=()=>{
                if(el.dataset.type==='Empty' && ['0','5'].includes(el.dataset.status)) {
                    const img=el.querySelector('img');
                    img.src=img.src.endsWith('_selected.png') ? '../img/standard_available.png' : '../img/standard_selected.png';
                }
            });
            const button = document.createElement('button');
            button.id='btnCheckOut'; button.textContent='Continue';
            button.onclick=()=>{document.querySelector('#divSeatMap').remove();button.remove();};
            document.body.append(button);
        }""")
        self.run_selection()
        self.assertEqual(self.page.locator('#divSeatMap').count(), 0)
        self.assertTrue(any('取消預選 E 排 18 號' in line for line in self.logs))

    def test_manual_leaves_page_untouched(self):
        self.settings.seat_mode = 'manual'
        self.settings.seat_preferred = 'B1,B2,B3'
        self.run_selection()
        self.assertFalse(any(s['selected'] for s in self.read()))
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_direction_breaks_equal_distance_tie(self):
        self.page.locator('td[data-type="Sold"]').evaluate_all("els => els.forEach(el => {el.dataset.type='Empty';el.dataset.status='0'})")
        self.settings.tickets = 1
        left = plan_seats(self.read(), self.settings)[0]
        self.settings.seat_direction = 'right'
        right = plan_seats(self.read(), self.settings)[0]
        self.assertEqual(left['gridRow'], right['gridRow'])
        self.assertLess(left['gridCol'], right['gridCol'])
        self.assertAlmostEqual(left['x'] + right['x'], 100)

    def test_back_and_custom_stay_in_range(self):
        self.settings.seat_mode = 'back'
        self.assertTrue(all(s['y'] >= 200/3 for s in plan_seats(self.read(), self.settings)))
        self.settings.seat_mode = 'custom'
        self.settings.seat_row_start, self.settings.seat_row_end = 10, 50
        self.settings.seat_col_start, self.settings.seat_col_end = 0, 20
        self.assertTrue(all(10 <= s['y'] <= 50 and 0 <= s['x'] <= 20 for s in plan_seats(self.read(), self.settings)))

    def test_no_contiguous_block_does_not_cross_aisles(self):
        self.settings.seat_mode = 'custom'
        self.settings.seat_col_start, self.settings.seat_col_end = 0, 100
        self.settings.tickets = 11
        with self.assertRaisesRegex(ValueError, '同排連座'):
            self.run_selection()
        self.assertFalse(any(s['selected'] for s in self.read()))
        self.settings.seat_contiguous = False
        self.run_selection()
        self.assertIn('測試完成', self.page.locator('#result').inner_text())

    def test_insufficient_seats_does_not_click_anything(self):
        self.page.locator('td[data-name]').evaluate_all("els => els.forEach(el => {el.dataset.type='Sold';el.dataset.status='3'})")
        with self.assertRaisesRegex(ValueError, '找不到'):
            self.run_selection()
        self.assertFalse(any(s['selected'] for s in self.read()))
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_rejected_click_never_continues(self):
        self.page.locator('td[data-name]').evaluate_all("els => els.forEach(el => el.onclick=()=>{})")
        with self.assertRaisesRegex(ValueError, '網站確認'):
            self.run_selection()
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_partial_selection_failure_is_preserved_for_handoff(self):
        plan = plan_seats(self.read(), self.settings)
        second = plan[1]
        self.page.locator(f"td[id='{second['row']}-{second['label']}']").evaluate('el => el.onclick=()=>{}')
        with self.assertRaises(ValueError):
            self.run_selection()
        self.assertEqual(sum(s['selected'] for s in self.read()), 1)
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_checkout_validation_failure_submits_once(self):
        self.page.locator('#btnCheckOut').evaluate('el => {window.clicks=0;el.onclick=()=>window.clicks++}')
        with self.assertRaisesRegex(ValueError, '下一步'):
            self.run_selection()
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_cancel_before_selection(self):
        self.stop.set()
        with self.assertRaises(Cancelled):
            self.run_selection()
        self.assertFalse(any(s['selected'] for s in self.read()))

    def test_multiple_areas_do_not_mix(self):
        self.page.locator('.Seating-Area').evaluate('el => el.after(el.cloneNode(true))')
        plan = plan_seats(self.read(), self.settings)
        self.assertEqual(len({s['area'] for s in plan}), 1)

    def test_backend_numbering_does_not_determine_physical_position(self):
        before = plan_seats(self.read(), self.settings)
        self.page.locator('td[data-name]').evaluate_all("els => els.forEach((el, i) => {el.dataset.row=String(i);el.dataset.seatnum=String(i+500)})")
        after = plan_seats(self.read(), self.settings)
        self.assertEqual([(s['row'], s['label']) for s in before], [(s['row'], s['label']) for s in after])

    def test_merged_blank_cell_is_rejected(self):
        self.page.locator('td:not([data-name])').first.evaluate('el => el.colSpan=2')
        with self.assertRaisesRegex(ValueError, '合併格'):
            self.run_selection()

    def test_racing_seat_sale_stops_without_checkout(self):
        plan = plan_seats(self.read(), self.settings)
        first, second = plan[:2]
        self.page.locator(f"td[id='{first['row']}-{first['label']}']").evaluate("""(el, nextId) => {
            const click = el.onclick;
            el.onclick = () => {click(); const next = document.getElementById(nextId);
                next.dataset.type='Sold'; next.dataset.status='3';};
        }""", f"{second['row']}-{second['label']}")
        with self.assertRaisesRegex(ValueError, '不可選'):
            self.run_selection()
        self.assertEqual(sum(s['selected'] for s in self.read()), 1)
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())

    def test_flow_continues_after_quantity_once(self):
        self.settings.seat_preferred = 'B1,B2,B3'
        self.context.route('https://sales.vscinemas.com.tw/**', lambda route: route.fulfill(
            content_type='text/html', body=resource('seats_fixture.html' if '/Seats' in route.request.url else 'quantity_fixture.html').read_text(encoding='utf-8')))
        self.page.goto('https://sales.vscinemas.com.tw/LiveTicketD4/')
        flow = TicketFlow(self.settings, lambda *event: self.logs.append(event), self.stop)
        flow.pending = False
        flow.post_pending = True
        flow.booking_page = self.page
        flow.tick(self.context.pages)
        self.assertIn('測試完成', self.page.locator('#result').inner_text())
        self.assertIn('B-1、B-2、B-3', self.page.locator('#result').inner_text())
        before = list(self.logs)
        flow.tick(self.context.pages)
        self.assertEqual(self.logs, before)
        self.assertTrue(any(isinstance(event, tuple) and event[0] == 'handoff' for event in self.logs))

    def test_flow_seat_failure_hands_off_without_retry(self):
        self.settings.tickets = 11
        self.context.route('https://sales.vscinemas.com.tw/**', lambda route: route.fulfill(
            content_type='text/html', body=resource('seats_fixture.html' if '/Seats' in route.request.url else 'quantity_fixture.html').read_text(encoding='utf-8')))
        self.page.goto('https://sales.vscinemas.com.tw/LiveTicketD4/')
        self.page.locator('[data-pricecode]').evaluate("el => el.add(new Option('11','11'))")
        flow = TicketFlow(self.settings, lambda *event: self.logs.append(event), self.stop)
        flow.pending, flow.post_pending, flow.booking_page = False, True, self.page
        flow.tick(self.context.pages)
        self.assertTrue(self.page.locator('#btnCheckOut').is_visible())
        self.assertTrue(any(event[0] == 'handoff' and '暫停' in event[1] for event in self.logs))
        before = list(self.logs)
        flow.tick(self.context.pages)
        self.assertEqual(self.logs, before)


if __name__ == '__main__':
    unittest.main()
