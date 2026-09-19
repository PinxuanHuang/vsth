# 電影購票助手

本次修正一般票種收合區塊展開的執行檔位於 `dist/expand-ticket/MovieTicketAssistant.exe`。原本 `dist/MovieTicketAssistant.exe` 因無法覆寫而保留舊版，請改開 `expand-ticket` 資料夾內的新版。

繁體中文 Windows 桌面程式，使用 Python 3.11、Tkinter 與 Playwright。目標為 Windows 10／11 x64，電腦需已安裝 Microsoft Edge；執行打包版本不需安裝 Python。

## 使用

1. 開啟 `dist/expand-ticket/MovieTicketAssistant.exe`（本次新版）。
2. 輸入起始網址，從下拉選單選影城，選擇場次的年／月／日／時／分（24 小時制、台灣場次時間），設定票數與是否自動勾選「我同意」。日期必須有效，日選單會隨月份與閏年調整。
3. 按「儲存並執行」，程式會開啟獨立 Edge 視窗，顯示「等待你手動進入購票專區」。
4. 自行點選要購票的專區。進入威秀的 `vsTicketingSP` 或 `vsTicketingSP數字` 購票頁後，程式會自動選影城，再點電影清單第一項，完成後保留瀏覽器。路徑取自實際進入的專區，不固定為第一專區；一般 `vsTicketing` 入口仍等待你選擇搶票專區。原分頁導覽與另開分頁皆支援，不需再按按鈕。
5. 選完第一部電影後，會比對指定日期與時間，進入該場次入口。啟用自動同意時，會勾選一般票種規定並送出；未啟用時，等待你自行同意並前往購票頁。程式接著設定票數並點「繼續」，交由你操作後續步驟。瀏覽器與程式保持開啟，完成後不再自動操作。
6. 按「停止並關閉瀏覽器」結束工作階段。關閉主視窗亦會關閉由本程式開啟的瀏覽器，不影響原本的 Edge。

## 目前範圍與網站適配

目前實作影城、第一部電影、指定場次、一般票種規定、票數與「繼續」操作。到下一步後由使用者接手選位或其他流程；不會自動結束瀏覽器。請保持主程式開啟，只有明確按「停止並關閉瀏覽器」或關閉主程式時才結束瀏覽器工作階段。

### 同意、票數與接手

- 自動同意嚴格限定 `#bookNormal input#agree[type="checkbox"]`，送出也限定同區塊的 `input[type="submit"]`。表單透過實際按鈕提交，沿用 action、hidden 欄位與網站驗證，不固定 LiveTicket 的版本路徑。
- 未啟用自動同意，不會勾選或送出，會持續等待使用者手動進入購票頁。若需要登入或驗證，請在瀏覽器操作，購票選單出現後自動接續。
- 先以 `.panel-title` 完整文字「一般票種」定位所屬 `.panel`；以內容的 class、實際高度及可見狀態判斷是否展開，不只依賴 aria-expanded；尚未展開時點擊標題並等待動畫，若事件無效則僅同步修正已核對的一般票種內容區塊（collapse in、高度 auto、aria-expanded=true）及標題的 collapsed 狀態，再找到該區塊 `table tr` 中，`td .spName` 或 `td` 文字完整符合「全票」的列，僅操作該列內可見的 `select.form-control-s`，以 option value 選擇票數。影廳、票種及收合區塊 ID 均不寫死，也不要求 data-hocode 或 data-pricecode。若有多個全票列，或全票列內仍有多個票數選單，流程停止提示手動確認；進階設定的票數 CSS 選擇器也只在一般票種的全票列內生效。其他票種（包含收合區塊）已有張數時會停止。
- 設定張數超出網站選項時會顯示可選張數，不會偷偷減少張數。成功後只點一次 `a#btnDoNext`，等待網址或步驟切換，再顯示「請在瀏覽器接手」。網站驗證未通過時顯示等待逾時，保留頁面，不重複提交。
- 接手後「重新套用」會停用，避免意外重跑；程式持續執行。若流程異常，瀏覽器也會保留供手動完成。

### 指定日期與時間

設定以 `YYYY-MM-DD HH:mm` 儲存，例如 `2026-09-25 19:20`。舊設定檔仍可讀取，第一次使用新版請補選場次日期與時間；本機舊版表單示範不要求場次設定。

程式讀取 `section.movieTime .movieDay` 中的 `h4` 日期，僅比對 `ul.bookList > li > a` 的時間，排除旁邊的座位查詢圖示連結。只有完整日期與時間相符且唯一的場次才會繼續。無相符場次會列出頁面場次；重複時間、停用場次或連結影城／專區不符時會停下。日期時間完全依頁面顯示比對，不自動改選鄰近場次，也不把 24:00 猜成次日 00:00。

例如第二專區頁面上的 `booking.aspx?cinemacode=21&txtSessionId=165195` 會以該頁 URL 為基準解析為 `https://www.vscinemas.com.tw/vsTicketingSP2/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195`。場次代碼讀取自實際頁面，不寫死。載入後保留瀏覽器供下一步操作，網站若要求登入亦交由使用者處理。

影城選項依本次提供的圖片排列，代碼已由官網選單核對如下，以 option value 操作 `#theater`，不依名稱猜測：

| 影城 | 值 |
| --- | --- |
| 台北信義威秀影城 | `1\|TP` |
| MUVIE CINEMAS 台北松仁 | `21\|MU` |
| 台北京站威秀影城 | `12\|QS` |
| 台北西門威秀影城 | `34\|TX` |
| 板橋大遠百威秀影城 | `16\|BQ` |
| 新店裕隆城威秀影城 | `32\|HU` |
| 林口MITSUI OUTLET PARK威秀影城 | `19\|LK` |
| 桃園統領威秀影城 | `20\|TY` |
| 新竹大遠百威秀影城 | `2\|HS` |
| 新竹巨城威秀影城 | `14\|BC` |
| MUVIE CINEMAS 台中TIGER CITY | `3\|TT01` |
| 台南大遠百威秀影城 | `5\|TN` |
| 台南南紡威秀影城 | `17\|NF` |
| 高雄大遠百威秀影城 | `6\|KS` |

選影城後等待網址中的 cinema 值更新，再點 `#movieListBox1 .movieList > li` 第一項內的連結，並確認網址的 movie 值。電影名稱與代碼不寫死。若沒有電影、頁面未更新或連結影城不符，流程暫停並顯示錯誤，不自動重複點擊。等待使用者進入購票專區不設期限；頁面內每個等待階段上限 30 秒。

### 多專區網址修正

瀏覽器確認第二專區選高雄後為 `/vsTicketingSP2/ticketing/ticket.aspx?cinema=6|KS`，第一部電影的 href 為 `?cinema=6|KS&movie=HO00017903`；第三專區對應 `/vsTicketingSP3/ticketing/ticket.aspx?cinema=6|KS`，第一部電影的 href 為 `?cinema=6|KS&movie=HO00017905`。這些電影代碼僅為本次觀察紀錄，程式不寫死它們。

程式辨識 `vsTicketingSP` 加可選數字的路徑，選影城時透過網站下拉選單觸發原本的導頁；電影連結用目前頁面 URL 與實際 href 解析，保留該專區路徑。後續網址與電影連結都必須留在原專區，防止誤跳回第一專區。

各專區提供的影城不同。選定影城不在該頁 `#theater` 選項時，紀錄會列出目前可選影城，停止流程；不會擅自改選別家。此次第二專區實測可選台北信義與高雄大遠百，現有設定清單可用高雄大遠百測試第二、第三專區。

舊設定若含清單外影城會清空影城欄位，請重新選擇。「載入示範設定」為舊版本機表單範例，可測試影城／票數／同意欄位；影城與同意 CSS 僅供該示範使用，票數 CSS 同時適用真實購票頁。

設定自動存於 `%APPDATA%/MovieTicketAssistant/settings.json`，不保存登入密碼或瀏覽器登入狀態。設定檔以明文保存網址，請勿輸入帶有私人登入憑證的連結。執行時鎖定設定，修改前請先停止。停止可能等待目前操作逾時（導覽最長 30 秒）。

## 開發與打包

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
powershell -ExecutionPolicy Bypass -File .\build.ps1
# 舊版執行檔使用中時可輸出到其他資料夾：
powershell -ExecutionPolicy Bypass -File .\build.ps1 -OutputDirectory dist/expand-ticket
```

產物：`dist/MovieTicketAssistant.exe`。請在 Windows x64 上打包；瀏覽器使用電腦已安裝的 Edge，不額外內含整套瀏覽器。執行檔未簽章。Windows 10 與 Windows 11 各版本的相容性仍需在目標電腦驗證。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py --smoke-test test-results/source-smoke.json
Start-Process -FilePath .\dist\MovieTicketAssistant.exe -ArgumentList '--smoke-test', 'test-results/exe-smoke.json' -Wait
```

`--smoke-test` 驗證 Tkinter、日期時間、三專區、指定場次、同 scope 同意表單、動態 ID 票數選單與繼續後保留瀏覽器。使用攔截回應的本機 HTML，不對真實網站下單或接受真實條款。成功時輸出 JSON。請先建立 `test-results` 資料夾。

技術參考：[Playwright 的 Edge 支援](https://playwright.dev/python/docs/browsers)、[PyInstaller 打包說明](https://playwright.dev/python/docs/library#pyinstaller)。

## 本次驗證

自動測試涵蓋14 家影城值對應、等待手動導覽、新分頁、只執行一次、重新套用、無電影、取消、錯誤影城連結及原有表單操作。另透過瀏覽器確認真實網站選擇新竹巨城後，可點第一部電影並進入含 movie 參數的頁面。真實 EXE 操作流程仍待使用者手動驗收。

受限建置環境測試時，Tcl/Tk 使用 `build/tcl-runtime` 的本機副本，EXE 測試的 TEMP/TMP 指向可寫入的 `test-results/runtime-temp`。此結果不代表已完成 Windows 10／11 全版本驗證。
