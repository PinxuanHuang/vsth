import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Settings, load_settings, save_settings


class FallbackSettingsTests(unittest.TestCase):
    def test_roundtrip_and_old_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(url='demo://ticket', cinema='test', fallback_ticket_type='special_single_package',
                                eslite_fallback_ticket_type='member', miramar_fallback_ticket_type='imax_full_price')
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text(json.dumps({'url': 'demo://ticket', 'cinema': 'test'}), encoding='utf-8')
            old = load_settings(path)
            self.assertEqual((old.fallback_ticket_type, old.eslite_fallback_ticket_type, old.miramar_fallback_ticket_type), ('', '', ''))

    def test_invalid_fallbacks(self):
        for key in ('fallback_ticket_type', 'eslite_fallback_ticket_type', 'miramar_fallback_ticket_type'):
            for value in ('unknown', None, [], 0):
                with self.subTest(key=key, value=value):
                    settings = Settings(url='demo://ticket', cinema='test')
                    setattr(settings, key, value)
                    with self.assertRaises(ValueError):
                        settings.validate()

    def test_ui_switching_save_and_lock(self):
        from app import App
        settings = Settings(url='demo://ticket', cinema='test', fallback_ticket_type='special_single_package',
                            eslite_fallback_ticket_type='member', miramar_fallback_ticket_type='imax_full_price')
        with patch('app.load_settings', return_value=settings):
            app = App()
        try:
            for site, label in (('威秀', '優惠套票／特殊映演單人套票'), ('誠品', '誠品票種／誠品會員'),
                                ('美麗華影城', '美麗華票種／網路IMAX全票')):
                app.booking_site.set(site)
                app.update_booking_site()
                self.assertEqual(app.fallback_ticket_combo.get(), label)
                app.busy(True)
                self.assertEqual(str(app.fallback_ticket_combo['state']), 'disabled')
                app.busy(False)
                self.assertEqual(str(app.fallback_ticket_combo['state']), 'readonly')
            self.assertEqual(app.selected_fallback_ticket_types(), {
                'fallback_ticket_type': settings.fallback_ticket_type,
                'eslite_fallback_ticket_type': settings.eslite_fallback_ticket_type,
                'miramar_fallback_ticket_type': settings.miramar_fallback_ticket_type})
            app.geometry('720x800')
            app.update()
            for widget in (app.ticket_combo, app.fallback_ticket_combo, app.start_button):
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), app.winfo_rootx() + app.winfo_width())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), app.winfo_rooty() + app.winfo_height())
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()
