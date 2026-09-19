"""Validated, versioned desktop settings; no browser credentials are persisted."""
import json
import os
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


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


def parse_showtime(value):
    if not isinstance(value, str):
        raise ValueError("場次日期時間格式錯誤。")
    try:
        result = datetime.strptime(value, "%Y-%m-%d %H:%M")
        if result.strftime("%Y-%m-%d %H:%M") != value:
            raise ValueError()
        return result
    except ValueError:
        raise ValueError("請選擇有效場次日期時間（年/月/日 時:分，24 小時制）。") from None


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

    def validate(self):
        if self.showtime:
            parse_showtime(self.showtime)
        elif not isinstance(self.showtime, str):
            raise ValueError("場次日期時間格式錯誤。")
        for name in ("url", "cinema", "cinema_selector", "tickets_selector", "agree_selector"):
            if not isinstance(getattr(self, name), str):
                raise ValueError("設定文字格式錯誤。")
        parts = urlsplit(self.url)
        if self.url != "demo://ticket" and (
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
    result = Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    result.validate()
    return result


def save_settings(settings, path=None):
    settings.validate()
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
