"""Ticket definitions shared by settings, the UI and the quantity-selection flow.

Add products with the same one-ticket-per-unit select workflow to TICKET_TYPES.
Keep site-specific behavior in the adapter, not in these display/matching values.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketType:
    category: str
    name: str

    @property
    def label(self):
        return f'{self.category}／{self.name}'


DEFAULT_TICKET_TYPE = 'full_price'
TICKET_TYPES = {
    DEFAULT_TICKET_TYPE: TicketType('一般票種', '全票'),
    'special_single_package': TicketType('優惠套票', '特殊映演單人套票'),
}

DEFAULT_ESLITE_TICKET_TYPE = 'full_price'
ESLITE_TICKET_TYPES = {
    DEFAULT_ESLITE_TICKET_TYPE: TicketType('誠品票種', '全票'),
    'cinephile': TicketType('誠品票種', '迷影卡卡友'),
    'concession': TicketType('誠品票種', '愛心/敬老票'),
    'member': TicketType('誠品票種', '誠品會員'),
    'student_military_police': TicketType('誠品票種', '學生/軍警票'),
    'single_package': TicketType('誠品票種', '單人套票'),
}


def get_ticket_type(key):
    if not isinstance(key, str) or key not in TICKET_TYPES:
        raise ValueError('票種設定無效，請重新選擇購票票種。')
    return TICKET_TYPES[key]


def get_eslite_ticket_type(key):
    if not isinstance(key, str) or key not in ESLITE_TICKET_TYPES:
        raise ValueError('誠品票種設定無效，請重新選擇購票票種。')
    return ESLITE_TICKET_TYPES[key]


DEFAULT_MIRAMAR_TICKET_TYPE = 'event'
MIRAMAR_TICKET_TYPES = {
    'event': TicketType('美麗華票種', '活動票'),
    'dolby_senior': TicketType('美麗華票種', 'DOLBY敬老票'),
    'dolby_disability': TicketType('美麗華票種', 'DOLBY愛心票'),
    'double_hotdog': TicketType('美麗華票種', '雙人電影熱狗套票'),
    'double_churros': TicketType('美麗華票種', '雙人電影吉拿套票'),
    'single_hotdog': TicketType('美麗華票種', '單人電影熱狗套票'),
    'single_churros': TicketType('美麗華票種', '單人電影吉拿套票'),
    'full_price': TicketType('美麗華票種', '網路全票'),
    'student': TicketType('美麗華票種', '網路 學生/軍警票'),
    'senior': TicketType('美麗華票種', '敬老票'),
    'disability': TicketType('美麗華票種', '愛心票'),
    'voucher': TicketType('美麗華票種', '團體電影優惠券(IMAX、杜比影院及3D須加價)'),
    'dolby_double_hotdog': TicketType('美麗華票種', 'DOLBY雙人熱狗套票'),
    'dolby_double_churros': TicketType('美麗華票種', 'DOLBY雙人吉拿套票'),
    'dolby_single_hotdog': TicketType('美麗華票種', 'DOLBY單人熱狗套票'),
    'dolby_single_churros': TicketType('美麗華票種', 'DOLBY單人吉拿套票'),
    'dolby_full_price': TicketType('美麗華票種', '網路DOLBY全票'),
    'dolby_student': TicketType('美麗華票種', '網路DOLBY 學生/軍警票'),
    'imax_double_hotdog': TicketType('美麗華票種', 'IMAX雙人熱狗套票'),
    'imax_double_churros': TicketType('美麗華票種', 'IMAX雙人吉拿套票'),
    'imax_single_hotdog': TicketType('美麗華票種', 'IMAX單人熱狗套票'),
    'imax_single_churros': TicketType('美麗華票種', 'IMAX單人吉拿套票'),
    'imax_full_price': TicketType('美麗華票種', '網路IMAX全票'),
    'imax_student': TicketType('美麗華票種', '網路IMAX 學生/軍警票'),
    'imax_senior': TicketType('美麗華票種', 'IMAX敬老票'),
    'imax_disability': TicketType('美麗華票種', 'IMAX愛心票'),
}


def get_miramar_ticket_type(key):
    if not isinstance(key, str) or key not in MIRAMAR_TICKET_TYPES:
        raise ValueError('美麗華票種設定無效，請重新選擇購票票種。')
    return MIRAMAR_TICKET_TYPES[key]
