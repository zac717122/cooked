# cooked
my academic is cooked.

把 **NCKU Moodle 作業**（和其他行事曆）合併成一個日曆，用 Google 日曆 / iPhone / Outlook 訂閱，手機電腦都會同步。

## 為什麼不用 Firecrawl 爬網頁？

Moodle 要登入才看得到作業，爬網頁要處理登入、而且網頁一改版就壞。Moodle 本身就有兩個官方管道：

| 方式 | 需要什麼 | 說明 |
| --- | --- | --- |
| **匯出行事曆網址（iCal）** ✅ 推薦 | 只要在 Moodle 點幾下 | 最穩定，不用帳密 |
| Moodle Web Service API | token | 跟 Moodle App 同一套 API，資訊多一點（作業連結） |

## 方法一：5 分鐘搞定（不用寫程式）

1. 登入 <https://moodle.ncku.edu.tw> → 右上角 **行事曆**（或 `/calendar/view.php`）→ 下方 **匯出行事曆**（Export calendar）。
2. 選 **所有課程** + **最近和未來 60 天**（或「近期」）→ 按 **取得行事曆網址**，複製那串網址（裡面有 `authtoken`，**不要分享給別人**）。
3. 訂閱：
   - **Google 日曆**（電腦版）：左側「其他日曆」＋ → **使用網址新增** → 貼上。
   - **iPhone / Mac**：設定 → 行事曆 → 帳號 → 加入帳號 → 其他 → **加入已訂閱的行事曆**。
   - **Outlook**：新增行事曆 → 從網際網路訂閱。

> ⚠️ Google 日曆更新訂閱的頻率很慢（大約 8–24 小時一次），iPhone 可以自己設定每 15 分鐘 / 每小時。

如果你只有 Moodle 一個來源，做到這裡就夠了。

## 方法二：合併多個來源 + 自動加課程名稱、提醒

`calsync.py` 會：

- 抓 Moodle（iCal 或 API）+ 任意其他 `.ics`（學校行事曆、社團、Google 日曆私人網址…）
- 標題自動加上 `[課程代碼]`，重複的事件只留一個
- 每個作業加上「前一天」和「3 小時前」提醒（可在設定檔改）
- 只用 Python 標準函式庫，不用 `pip install`

### 在自己電腦跑

```bash
cp config.example.json config.json
export MOODLE_ICAL_URL='貼上方法一拿到的網址'
python3 calsync.py --list     # 終端機列出接下來的作業（🔥 24 小時內、⚠️ 3 天內）
python3 calsync.py            # 產生 calendar.ics
```

### 用 GitHub Actions 自動每 2 小時更新

1. 到 <https://gist.github.com> 建一個 **Secret gist**，檔名 `calendar.ics`，內容隨便打，記下網址最後那串 ID。
2. 到 GitHub → Settings → Developer settings → **Personal access tokens (classic)**，建一個只勾 `gist` 權限的 token。
3. 在這個 repo → Settings → Secrets and variables → Actions，新增：
   - `MOODLE_ICAL_URL`：方法一的網址
   - `GIST_TOKEN`：剛剛的 token
   - `GIST_ID`：gist 的 ID
   - （選用）`EXTRA_ICS_URL`、`MOODLE_TOKEN`
4. Actions 分頁 → **Sync calendar** → Run workflow 測試一次。
5. 日曆 App 訂閱 `https://gist.githubusercontent.com/<你的帳號>/<GIST_ID>/raw/calendar.ics`。

要開其他來源，複製 `config.example.json` 成 `config.json` 把對應來源的 `"enabled": false` 拿掉。
（`config.json` 預設被 `.gitignore` 排除；要讓 Actions 用到，記得網址都寫成 `${環境變數}` 再 commit，不要把真網址寫進去。）

### （選用）Moodle API token

成大 Moodle 若有開放行動 App，可以在 Moodle → 個人偏好 → **安全金鑰**（`/user/managetoken.php`）找到 `Moodle mobile web service` 的金鑰，填進 `MOODLE_TOKEN` 並把 `moodle_api` 來源打開。
如果找不到這個頁面就用 iCal 方式即可。

## 測試

```bash
python3 -m unittest discover -s tests
```
