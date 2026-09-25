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


def get_ticket_type(key):
    if not isinstance(key, str) or key not in TICKET_TYPES:
        raise ValueError('票種設定無效，請重新選擇購票票種。')
    return TICKET_TYPES[key]
