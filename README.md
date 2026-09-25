# 電影購票助手

本次指定座位版位於 `dist/preferred-seats/MovieTicketAssistant.exe`，可輸入優先座位，並沿用實際網站的 `SelectSeats` 先進先出機制替換預選座位。

繁體中文 Windows 桌面程式，使用 Python 3.11、Tkinter 與 Playwright。目標為 Windows 10／11 x64，電腦需已安裝 Microsoft Edge；執行打包版本不需安裝 Python。

## 使用

1. 開啟 `dist/preferred-seats/MovieTicketAssistant.exe`（本次新版）。
2. 輸入起始網址，從下拉選單選影城，選擇場次的年／月／日及「第一場／最後一場」，設定票數與是否自動勾選「我同意」。日期必須有效，日選單會隨月份與閏年調整。
3. 按「儲存並執行」，程式會開啟獨立 Edge 視窗，顯示「等待你手動進入購票專區」。
4. 自行點選要購票的專區。進入威秀的 `vsTicketingSP` 或 `vsTicketingSP數字` 購票頁後，程式會自動選影城，再點電影清單第一項，完成後保留瀏覽器。路徑取自實際進入的專區，不固定為第一專區；一般 `vsTicketing` 入口仍等待你選擇搶票專區。原分頁導覽與另開分頁皆支援，不需再按按鈕。
5. 選完第一部電影後，會比對指定日期並依設定選擇第一場或最後一場，進入該場次入口。啟用自動同意時，會勾選一般票種規定並送出；未啟用時，等待你自行同意並前往購票頁。程式接著設定票數並點「繼續」。座位偏好為「手動選位」時交由你選位；其他偏好會自動選足指定張數，再點選位頁的「繼續」。後續流程由你接手，瀏覽器保持開啟。
6. 按「停止並關閉瀏覽器」結束工作階段。關閉主視窗亦會關閉由本程式開啟的瀏覽器，不影響原本的 Edge。

## 目前範圍與網站適配

目前實作影城、第一部電影、指定場次、一般票種規定、票數、選位偏好、「繼續」及結帳會員登入。付款方式、付款送出及額外驗證由使用者接手。請保持主程式開啟，只有明確按「停止並關閉瀏覽器」或關閉主程式時才結束瀏覽器工作階段。

### 結帳會員登入

- 在「會員登入」頁籤填入帳號（信箱）與密碼。密碼保留原始字元（包含前後空白），預設遮蔽，可勾選「顯示密碼」。未設定完整帳密時，遇到登入表單會交由使用者接手。
- 自動選位並按繼續後，等待新頁載入，再短暫等候登入表單出現。只辨識目前威秀購票 HTTPS 網域同來源、POST 方法、action 路徑以 `/Home/VieShowLoginForCheckout` 結尾的可見表單，不寫死 `LiveTicketD4`／`VieShowTicketD4` 前綴或 HTML ID。
- 該表單必須具有唯一 `input[name="UserName"][type="email"]`、`input[name="Password"][type="password"]` 與唯一可見的 submit 按鈕。只在該表單填值並點擊原送出按鈕，保留網站隱藏欄位及驗證，不操作頁首其他登入表單。
- 沒有這個表單時不填帳密、不送出任何付款操作，直接交由使用者操作後續頁面。出現多個表單、欄位不明確、長度不符、登入失敗、逾時或需額外驗證時，也保留頁面供手動確認。每次流程最多送出一次登入，不自動重試。
- 帳號與密碼不寫入日誌，登入操作的原始工具例外也不直接顯示，避免帶出填寫值。密碼以 Windows DPAPI 加密後存入設定檔，只能由對應 Windows 使用者解密；清空密碼並儲存即可刪除已存密碼。瀏覽器登入 cookie 不保存。
- 手動選位模式仍在選位頁交由使用者接手，不會在手動接手後繼續監控付款頁。

### 座位偏好（測試版）

- 預設「手動選位」，舊設定亦維持手動。自動模式有「前排中間」「中間排中間」「後排中間」及「自訂範圍」。
- 「優先座位（逗號分隔）」可填入 `N7,N8,N9,M7,M8,M9`，依輸入順序優先選取畫面上的排別與座號；不使用後端的反向座號比對。支援小寫、項目前後空白、全形逗號及 `N-7`，重複座位只計算一次。欄位會隨設定儲存，舊設定預設留空。
- 優先座位在自動選位模式生效，可超出目前前／中／後排或自訂百分比範圍。仍會跳過已售、不可選與輪椅座；符合的網站預選座位也可保留。優先順序以能湊足票數的組合為準，較早輸入的座位優先；票數不足時不會先點擊部分座位。
- 指定清單只有部分可選時，勾選「同排連座」會在該排補足相鄰座位，不跨走道或區域；相同優先程度時沿用接近範圍中心及左右方向偏好。取消「同排連座」則先選指定座位，再依原本範圍與順序補足，仍不混用不同區域。例如 3 張票填 `N7,N8,N9,M7,M8,M9`，N 排可連座時先選 N7～N9；N 排皆不可選且 M 排可連座時選 M7～M9。
- 清單留空、指定座位皆不存在／不可選，或無法依連座等設定湊足張數時，完整退回原本的選位邏輯；原規則也找不到足夠座位時保留頁面交由使用者操作。「手動選位」仍由使用者操作，不會因已儲存的優先座位自動選取。
- 前／中／後各取實際有座位排數的三分之一，左右取座位圖寬度的 25～75%。自訂範圍使用 0～100%，前後由銀幕側到後方、左右由左到右。例如前後 40～90%、左右 30～70%。以座位中心是否落在範圍內判斷，不足時不會擴大範圍。
- 依範例的上方銀幕配置，從 DOM 上到下判斷前後；每區獨立計算。全空白列不計入排數，空白欄保留供判斷走道。特殊旋轉、反向或合併格的影廳需手動確認；含合併座位格時停止自動選位。
- 排別與座號取自 `data-name`、`data-col`；位置依 `<tr>/<td>`，保留 `data-row`、`data-seatnum` 與區域代碼，不寫死影廳尺寸或反向索引公式。
- 搜尋順序為「範圍中心最近的排 → 後方各排由近到遠 → 前方各排由近到遠」。目標排依完整座位圖計算，包含已售座位；前後等距時取較前排作起點。預設模式與自訂範圍皆不超出指定區域。
- 預設「同排連座」：按上述排別順序找足夠張數的連續座位區塊，不跨空格、走道或區域；同一排以區塊中心接近範圍中心為優先，再從中心向兩側逐一點選。等距時依「左側優先／右側優先」。
- 取消「同排連座」後，先在目標排從中央向左右選位，不足再按上述順序到後排、前排補足；可能跨走道或分排，但不混合不同區域。
- 網站預選座位亦納入候選。先規劃足夠張數，再直接點擊尚未選取的目標座位。原站 `SelectSeats` 已滿額時會自動移除最早的座位；再次點擊已選座位不會取消。如果原本符合目標的預選被移除，程式會重新補選，直到清單完整符合規劃。若範圍內不足則保留原預選交由使用者操作。
- 每次點擊前後記錄操作與網站目前選位，同時核對 `SelectSeats` 陣列及座位圖片；不直接改寫網站陣列、DOM 或呼叫保留座位 API。沒有 `SelectSeats` 的本機舊示範才使用切換選位模式。
- `data-status` 在原站點擊後不會更新，因此選取狀態依圖片核對。選足張數、網站清單正確且繼續按鈕沒有 `disabled` class 時，才點擊 `#btnCheckOut`，由網站原事件執行 CheckSeats、ReserveSeats 及下一步導頁。失敗則保留頁面且不重複提交；導至 Error 頁不會回報成功。
- 可按「載入座位測試」，調整偏好與張數後「儲存並執行」，在本機座位圖驗證結果；此模式不連線購票。切回真實流程前需填回購票網址、影城與日期。

建置：`powershell -ExecutionPolicy Bypass -File .\build.ps1 -OutputDirectory dist/preferred-seats`。

選位回歸測試包含使用者提供的原站 click handler，以及頁面引用的 jQuery 1.11.1（`tests/jquery.site.min.js`，保留原始授權標頭）。這些測試只在本機執行，不對真實網站送出訂票。

指定座位測試另使用此次提供的不同影廳 HTML（`tests/preferred_seat_map_fixture.html`），涵蓋已售時回退、可選時依 N 排／M 排順序優先、部分座位補足、走道、跨區限制、預選替換及設定存取。

### 同意、票數與接手

- 自動同意嚴格限定 `#bookNormal input#agree[type="checkbox"]`，送出也限定同區塊的 `input[type="submit"]`。表單透過實際按鈕提交，沿用 action、hidden 欄位與網站驗證，不固定 LiveTicket 的版本路徑。
- 未啟用自動同意，不會勾選或送出，會持續等待使用者手動進入購票頁。若需要登入或驗證，請在瀏覽器操作，購票選單出現後自動接續。
- 先以 `.panel-title` 完整文字「一般票種」定位所屬 `.panel`；以內容的 class、實際高度及可見狀態判斷是否展開，不只依賴 aria-expanded；尚未展開時點擊標題並等待動畫，若事件無效則僅同步修正已核對的一般票種內容區塊（collapse in、高度 auto、aria-expanded=true）及標題的 collapsed 狀態，再找到該區塊 `table tr` 中，`td .spName` 或 `td` 文字完整符合「全票」的列，僅操作該列內可見的 `select.form-control-s`，以 option value 選擇票數。影廳、票種及收合區塊 ID 均不寫死，也不要求 data-hocode 或 data-pricecode。若有多個全票列，或全票列內仍有多個票數選單，流程停止提示手動確認；進階設定的票數 CSS 選擇器也只在一般票種的全票列內生效。其他票種（包含收合區塊）已有張數時會停止。
- 設定張數超出網站選項時會顯示可選張數，不會偷偷減少張數。成功後只點一次 `a#btnDoNext`，等待網址或步驟切換；有座位偏好則接續自動選位，沒有則交由使用者接手。自動選足座位並逐一確認後，才點一次 `button#btnCheckOut`。網站驗證未通過時顯示等待逾時，保留頁面，不重複提交。
- 接手後「重新套用」會停用，避免意外重跑；程式持續執行。若流程異常，瀏覽器也會保留供手動完成。

### 指定日期與第一場／最後一場

設定以 `YYYY-MM-DD` 儲存，例如 `2026-09-25`；`session_position` 為 `first`（第一場，預設）或 `last`（最後一場）。舊設定檔的日期時間會保留日期、移除時間，預設選第一場；本機表單示範不要求場次設定。

本次新版執行檔：`dist/session-position/MovieTicketAssistant.exe`。

程式讀取 `section.movieTime .movieDay` 中的 `h4` 日期，找到該日期的 `ul.bookList`，依設定取清單中第一個或最後一個直接子 `<li>` 內的 `<a href>`，排除旁邊的座位查詢圖示連結。完全依網頁排列順序選擇，不排序或比對時間文字。找不到日期、同日期有多個清單、指定首尾項無法訂票，或連結影城／專區不符時會停下，不跳過首尾項改選其他場次。其餘購票流程維持原樣。

例如第二專區頁面上的 `booking.aspx?cinemacode=21&txtSessionId=165195` 會以該頁 URL 為基準解析為 `https://www.vscinemas.com.tw/vsTicketingSP2/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195`。場次代碼讀取自實際頁面，不寫死。載入後保留瀏覽器供下一步操作，網站若要求登入亦交由使用者處理。

影城選項依本次提供的圖片排列，代碼已由官網選單核對如下，以 option value 操作 `#theater`，不依名稱猜測：

| 影城                           | 值        |
| ------------------------------ | --------- |
| 台北信義威秀影城               | `1\|TP`   |
| MUVIE CINEMAS 台北松仁         | `21\|MU`  |
| 台北京站威秀影城               | `12\|QS`  |
| 台北西門威秀影城               | `34\|TX`  |
| 板橋大遠百威秀影城             | `16\|BQ`  |
| 新店裕隆城威秀影城             | `32\|HU`  |
| 林口MITSUI OUTLET PARK威秀影城 | `19\|LK`  |
| 桃園統領威秀影城               | `20\|TY`  |
| 新竹大遠百威秀影城             | `2\|HS`   |
| 新竹巨城威秀影城               | `14\|BC`  |
| MUVIE CINEMAS 台中TIGER CITY   | `3\|TT01` |
| 台南大遠百威秀影城             | `5\|TN`   |
| 台南南紡威秀影城               | `17\|NF`  |
| 高雄大遠百威秀影城             | `6\|KS`   |

選影城後等待網址中的 cinema 值更新，再點 `#movieListBox1 .movieList > li` 第一項內的連結，並確認網址的 movie 值。電影名稱與代碼不寫死。若沒有電影、頁面未更新或連結影城不符，流程暫停並顯示錯誤，不自動重複點擊。等待使用者進入購票專區不設期限；頁面內每個等待階段上限 30 秒。

### 多專區網址修正

瀏覽器確認第二專區選高雄後為 `/vsTicketingSP2/ticketing/ticket.aspx?cinema=6|KS`，第一部電影的 href 為 `?cinema=6|KS&movie=HO00017903`；第三專區對應 `/vsTicketingSP3/ticketing/ticket.aspx?cinema=6|KS`，第一部電影的 href 為 `?cinema=6|KS&movie=HO00017905`。這些電影代碼僅為本次觀察紀錄，程式不寫死它們。

程式辨識 `vsTicketingSP` 加可選數字的路徑，選影城時透過網站下拉選單觸發原本的導頁；電影連結用目前頁面 URL 與實際 href 解析，保留該專區路徑。後續網址與電影連結都必須留在原專區，防止誤跳回第一專區。

各專區提供的影城不同。選定影城不在該頁 `#theater` 選項時，紀錄會列出目前可選影城，停止流程；不會擅自改選別家。此次第二專區實測可選台北信義與高雄大遠百，現有設定清單可用高雄大遠百測試第二、第三專區。

舊設定若含清單外影城會清空影城欄位，請重新選擇。「載入示範設定」為舊版本機表單範例，可測試影城／票數／同意欄位；影城與同意 CSS 僅供該示範使用，票數 CSS 同時適用真實購票頁。

設定自動存於 `%APPDATA%/MovieTicketAssistant/settings.json`；登入密碼使用 Windows DPAPI 加密儲存，不保存瀏覽器登入 cookie。網址與登入信箱以文字保存。執行時鎖定設定，修改前請先停止。停止可能等待目前操作逾時（導覽最長 30 秒）。

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

`--smoke-test` 驗證 Tkinter、日期與首尾場次設定、三專區、指定場次、同 scope 同意表單、動態 ID 票數選單與繼續後保留瀏覽器。使用攔截回應的本機 HTML，不對真實網站下單或接受真實條款。成功時輸出 JSON。請先建立 `test-results` 資料夾。

技術參考：[Playwright 的 Edge 支援](https://playwright.dev/python/docs/browsers)、[PyInstaller 打包說明](https://playwright.dev/python/docs/library#pyinstaller)。

## 本次驗證

自動測試涵蓋14 家影城值對應、等待手動導覽、新分頁、只執行一次、重新套用、無電影、取消、錯誤影城連結及原有表單操作。另透過瀏覽器確認真實網站選擇新竹巨城後，可點第一部電影並進入含 movie 參數的頁面。真實 EXE 操作流程仍待使用者手動驗收。

受限建置環境測試時，Tcl/Tk 使用 `build/tcl-runtime` 的本機副本，EXE 測試的 TEMP/TMP 指向可寫入的 `test-results/runtime-temp`。此結果不代表已完成 Windows 10／11 全版本驗證。
