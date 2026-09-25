"""Validated desktop settings with Windows-protected login credentials."""
import json
import os
import re
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from credentials import protect_password, unprotect_password


CINEMAS = {
    "台北信義威秀影城": "1|TP",
    "MUVIE CINEMAS 台北松仁": "21|MU",
    "台北京站威秀影城": "12|QS",
    "台北西門威秀影城": "34|TX",
    "板橋大遠百威秀影城": "16|BQ",
    "新店裕隆城威秀影城": "32|HU",
    "林口MITSUI OUTLET PARK威秀影城": "19|LK",
    "桃園統領威秀影城": "20|TY",
    "新竹大遠百威秀影城": "2|HS",
    "新竹巨城威秀影城": "14|BC",
    "MUVIE CINEMAS 台中TIGER CITY": "3|TT01",
    "台南大遠百威秀影城": "5|TN",
    "台南南紡威秀影城": "17|NF",
    "高雄大遠百威秀影城": "6|KS",
}

SEAT_MODES = {"手動選位": "manual", "中間排中間": "middle", "後排中間": "back",
              "前排中間": "front", "自訂範圍": "custom"}


def parse_preferred_seats(value):
    if not isinstance(value, str):
        raise ValueError("優先座位請輸入文字，例如 N7,N8,N9,M7,M8,M9。")
    result = []
    for token in value.replace("，", ",").split(","):
        token = token.strip().upper()
        if not token:
            continue
        match = re.fullmatch(r"([A-Z]+)-?([0-9]+)", token)
        if not match or int(match[2]) < 1:
            raise ValueError(f"優先座位「{token}」格式錯誤，請以逗號分隔排別與座號，例如 N7,N8,N9。")
        seat = f"{match[1]}{int(match[2])}"
        if seat not in result:
            result.append(seat)
    return result


def parse_showtime(value):
    if not isinstance(value, str):
        raise ValueError("場次日期格式錯誤。")
    try:
        result = datetime.strptime(value, "%Y-%m-%d")
        if result.strftime("%Y-%m-%d") != value:
            raise ValueError()
        return result
    except ValueError:
        raise ValueError("請選擇有效場次日期（年/月/日）。") from None


@dataclass
class Settings:
    url: str = ""
    cinema: str = ""
    agree: bool = False
    tickets: int = 2
    cinema_selector: str = ""
    tickets_selector: str = ""
    agree_selector: str = ""
    showtime: str = ""
    session_position: str = "first"
    seat_mode: str = "manual"
    seat_row_start: int = 0
    seat_row_end: int = 100
    seat_col_start: int = 25
    seat_col_end: int = 75
    seat_direction: str = "left"
    seat_contiguous: bool = True
    seat_preferred: str = ""
    login_email: str = field(default='', repr=False)
    login_password: str = field(default='', repr=False)

    def validate_seats(self):
        parse_preferred_seats(self.seat_preferred)
        if self.seat_mode not in SEAT_MODES.values():
            raise ValueError("座位偏好格式錯誤。")
        if self.seat_direction not in ("left", "right") or type(self.seat_contiguous) is not bool:
            raise ValueError("選位方向或連座設定格式錯誤。")
        for axis in ("row", "col"):
            start, end = (getattr(self, f"seat_{axis}_{bound}") for bound in ("start", "end"))
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= 100:
                raise ValueError("座位範圍須為 0～100 的整數，起點必須小於終點。")

    def validate(self):
        self.validate_seats()
        if not isinstance(self.login_email, str) or not isinstance(self.login_password, str):
            raise ValueError('會員登入設定格式錯誤。')
        if self.login_email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', self.login_email):
            raise ValueError('會員帳號請輸入有效的電子郵件地址。')
        if self.showtime:
            parse_showtime(self.showtime)
        elif not isinstance(self.showtime, str):
            raise ValueError("場次日期格式錯誤。")
        if self.session_position not in ("first", "last"):
            raise ValueError("請選擇第一場或最後一場。")
        for name in ("url", "cinema", "cinema_selector", "tickets_selector", "agree_selector"):
            if not isinstance(getattr(self, name), str):
                raise ValueError("設定文字格式錯誤。")
        parts = urlsplit(self.url)
        if self.url not in ("demo://ticket", "demo://seats") and (
            parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password
        ):
            raise ValueError("請輸入完整的 http:// 或 https:// 網址（不可包含帳號密碼）。")
        if not self.cinema.strip():
            raise ValueError("請輸入影城名稱，文字須與網站選項一致。")
        if type(self.tickets) is not int or not 1 <= self.tickets <= 20:
            raise ValueError("票數必須是 1～20 的整數；實際上限仍以網站為準。")
        if type(self.agree) is not bool:
            raise ValueError("同意設定格式錯誤。")


def settings_path():
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "MovieTicketAssistant" / "settings.json"


def load_settings(path=None):
    path = path or settings_path()
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    # Preserve the date from settings saved by the former date/time picker.
    stamp = data.get("showtime")
    if isinstance(stamp, str) and len(stamp) == 16:
        parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        if parsed.strftime("%Y-%m-%d %H:%M") == stamp:
            data["showtime"] = parsed.strftime("%Y-%m-%d")
    data['login_password'] = unprotect_password(data.get('protected_login_password', ''))
    result = Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    result.validate()
    return result


def save_settings(settings, path=None):
    settings.validate()
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    data = asdict(settings)
    data['protected_login_password'] = protect_password(data.pop('login_password'))
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
