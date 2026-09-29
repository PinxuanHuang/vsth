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
