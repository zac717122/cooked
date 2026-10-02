#!/usr/bin/env python3
"""把 NCKU Moodle 作業 + 其他行事曆合併成一個 .ics，讓 Google / Apple / Outlook 訂閱。

只用 Python 標準函式庫，不用裝任何套件。

用法：
    python3 calsync.py                       # 讀 config.json，輸出 calendar.ics
    python3 calsync.py -c my.json -o out.ics
    python3 calsync.py --list                # 只在終端機列出接下來的作業

設定檔裡的字串可以寫 "${ENV_NAME}"，會從環境變數取值（給 GitHub Actions secrets 用）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PRODID = "-//cooked//moodle-calsync//ZH"
TW = timezone(timedelta(hours=8))
USER_AGENT = "cooked-calsync/1.0"


# ---------------------------------------------------------------- 設定

def expand_env(value):
    """把 "${NAME}" 換成環境變數；缺少時回傳空字串。"""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return expand_env(json.load(f))


# ---------------------------------------------------------------- ICS 工具

def fetch(url, data=None, timeout=30):
    if url.startswith("file://") or os.path.exists(url):
        path = url[len("file://"):] if url.startswith("file://") else url
        with open(path, encoding="utf-8") as f:
            return f.read()
    if data is not None:
        data = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def unfold(text):
    """RFC 5545：以空白或 tab 開頭的行是上一行的延續。"""
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def fold(line):
    """每行最多 75 bytes，超過就折行（不切斷 UTF-8 字元）。"""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        limit = 75 if not out else 74
        if len(cur) + len(b) > limit:
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def unescape(text):
    return (text.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def utc_stamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_blocks(lines, name):
    """回傳所有 BEGIN:name ... END:name 區塊（每個區塊是 list of lines）。"""
    blocks, cur, depth = [], None, 0
    for line in lines:
        if line == f"BEGIN:{name}":
            if cur is None:
                cur = []
            depth += 1
        if cur is not None:
            cur.append(line)
        if line == f"END:{name}":
            depth -= 1
            if depth == 0 and cur is not None:
                blocks.append(cur)
                cur = None
    return blocks


def prop(block, key):
    """取得區塊第一層的屬性值（忽略參數，例如 DTSTART;TZID=...）。"""
    for line in block:
        k, _, v = line.partition(":")
        if k.split(";")[0].upper() == key:
            return v
    return None


def parse_dt(value):
    """把 ICS 的時間轉成 UTC timestamp（只為了排序／過濾，抓不到就回傳 None）。"""
    if not value:
        return None
    v = value.strip()
    for fmt, tz in (("%Y%m%dT%H%M%SZ", timezone.utc), ("%Y%m%dT%H%M%S", TW), ("%Y%m%d", TW)):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=tz).timestamp()
        except ValueError:
            pass
    return None


class Event:
    """一個要輸出的 VEVENT。lines 是原始（已 unfold）的內容。"""

    def __init__(self, lines, source):
        self.lines = lines
        self.source = source

    @property
    def uid(self):
        return prop(self.lines, "UID") or str(hash(tuple(self.lines)))

    @property
    def summary(self):
        return unescape(prop(self.lines, "SUMMARY") or "")

    @property
    def start(self):
        for line in self.lines:
            k, _, v = line.partition(":")
            if k.split(";")[0] == "DTSTART":
                return parse_dt(v)
        return None

    def set_summary(self, text):
        self.lines = [("SUMMARY:" + escape(text)) if l.split(":")[0].split(";")[0] == "SUMMARY"
                      else l for l in self.lines]

    def has_alarm(self):
        return "BEGIN:VALARM" in self.lines

    def add_alarms(self, minutes_list):
        alarms = []
        for m in minutes_list:
            alarms += ["BEGIN:VALARM", "ACTION:DISPLAY",
                       "DESCRIPTION:" + escape(self.summary),
                       f"TRIGGER:-PT{int(m)}M", "END:VALARM"]
        self.lines = self.lines[:-1] + alarms + [self.lines[-1]]


# ---------------------------------------------------------------- 來源

def course_from_moodle_ical(block):
    """Moodle 匯出的 ICS 會把課程簡稱放在 CATEGORIES。"""
    cat = prop(block, "CATEGORIES")
    return unescape(cat) if cat else ""


def load_ics_source(src):
    """一般 ICS 網址（Moodle 匯出、Google 日曆私人網址、學校行事曆…）。"""
    text = fetch(src["url"])
    lines = unfold(text)
    events, tzs = [], parse_blocks(lines, "VTIMEZONE")
    is_moodle = src.get("type") == "moodle_ical"
    label = src.get("prefix", "")
    for block in parse_blocks(lines, "VEVENT"):
        ev = Event(block, src.get("name", src["url"]))
        tag = label
        if is_moodle and not tag:
            tag = course_from_moodle_ical(block)
        if tag and not ev.summary.startswith(f"[{tag}]"):
            ev.set_summary(f"[{tag}] {ev.summary}")
        events.append(ev)
    return events, tzs


def moodle_call(base_url, token, function, **params):
    url = base_url.rstrip("/") + "/webservice/rest/server.php"
    data = {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json"}
    data.update(params)
    result = json.loads(fetch(url, data=data))
    if isinstance(result, dict) and result.get("exception"):
        raise RuntimeError(f"{function}: {result.get('message') or result}")
    return result


def moodle_token(base_url, username, password, service="moodle_mobile_app"):
    url = base_url.rstrip("/") + "/login/token.php"
    result = json.loads(fetch(url, data={"username": username, "password": password,
                                         "service": service}))
    if "token" not in result:
        raise RuntimeError(f"拿不到 token：{result.get('error') or result}")
    return result["token"]


def load_moodle_api_source(src, now=None):
    """用 Moodle Web Service（跟 Moodle App 同一套 API）抓「時間軸」上的待辦。"""
    base = src["base_url"]
    token = src.get("token") or moodle_token(base, src["username"], src["password"])
    now = int(now or time.time())
    days_back = int(src.get("days_back", 7))
    data = moodle_call(base, token, "core_calendar_get_action_events_by_timesort",
                       timesortfrom=now - days_back * 86400, limitnum=int(src.get("limit", 50)))
    events = []
    for e in data.get("events", []):
        events.append(moodle_api_event(e, base))
    return events, []


def moodle_api_event(e, base_url):
    due = int(e.get("timesort") or e.get("timestart"))
    course = (e.get("course") or {}).get("shortname") or (e.get("course") or {}).get("fullname", "")
    name = e.get("name") or e.get("activityname") or "Moodle 事件"
    summary = f"[{course}] {name}" if course else name
    url = e.get("url") or ""
    desc_parts = [course and f"課程：{(e.get('course') or {}).get('fullname', course)}",
                  f"截止：{datetime.fromtimestamp(due, TW):%Y-%m-%d %H:%M}",
                  url]
    desc = "\n".join(p for p in desc_parts if p)
    host = urllib.parse.urlparse(base_url).hostname or "moodle"
    lines = ["BEGIN:VEVENT",
             f"UID:moodle-{e.get('id')}@{host}",
             f"DTSTAMP:{utc_stamp(time.time())}",
             f"DTSTART:{utc_stamp(due - 30 * 60)}",
             f"DTEND:{utc_stamp(due)}",
             "SUMMARY:" + escape(summary),
             "DESCRIPTION:" + escape(desc)]
    if url:
        lines.append("URL:" + url)
    lines.append("END:VEVENT")
    return Event(lines, "moodle_api")


LOADERS = {
    "ics": load_ics_source,
    "moodle_ical": load_ics_source,
    "moodle_api": load_moodle_api_source,
}


# ---------------------------------------------------------------- 合併 & 輸出

def collect(config, now=None):
    now = now or time.time()
    all_events, tzs, errors = {}, {}, []
    for src in config.get("sources", []):
        if src.get("enabled", True) is False:
            continue
        kind = src.get("type", "ics")
        if kind not in LOADERS:
            errors.append(f"未知的來源類型：{kind}")
            continue
        if not any(src.get(k) for k in ("url", "base_url")):
            errors.append(f"略過 {src.get('name', kind)}：沒有設定網址（環境變數是不是沒設？）")
            continue
        try:
            events, blocks = LOADERS[kind](src)
        except Exception as exc:  # 一個來源壞掉不要拖垮全部
            errors.append(f"{src.get('name', kind)} 讀取失敗：{exc}")
            continue
        for b in blocks:
            tzs.setdefault(prop(b, "TZID"), b)
        for ev in events:
            all_events.setdefault(ev.uid, ev)  # 同 UID（例如 iCal 跟 API 重複）只留第一個

    keep_after = now - int(config.get("keep_past_days", 30)) * 86400
    events = [e for e in all_events.values() if (e.start or now) >= keep_after]
    events.sort(key=lambda e: e.start or 0)

    alarms = config.get("alarm_minutes_before", [])
    if alarms:
        for e in events:
            if not e.has_alarm():
                e.add_alarms(alarms)
    return events, list(tzs.values()), errors


def render(events, tzs, name="課程行事曆"):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + escape(name), "X-WR-TIMEZONE:Asia/Taipei",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for b in tzs:
        lines += b
    for e in events:
        lines += e.lines
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def print_agenda(events, now=None):
    now = now or time.time()
    upcoming = [e for e in events if (e.start or 0) >= now]
    if not upcoming:
        print("接下來沒有事件 🎉")
        return
    for e in upcoming:
        when = datetime.fromtimestamp(e.start, TW).strftime("%m/%d (%a) %H:%M")
        days = (e.start - now) / 86400
        flag = "🔥" if days < 1 else ("⚠️ " if days < 3 else "  ")
        print(f"{flag} {when}  {e.summary}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("-o", "--output", help="輸出 .ics 路徑（預設看設定檔，否則 calendar.ics）")
    ap.add_argument("--list", action="store_true", help="只列出接下來的事件，不寫檔")
    args = ap.parse_args(argv)

    if not os.path.exists(args.config):
        sys.exit(f"找不到 {args.config}，先複製 config.example.json 改成 config.json 吧")
    config = load_config(args.config)
    events, tzs, errors = collect(config)
    for err in errors:
        print("⚠️  " + err, file=sys.stderr)

    if args.list:
        print_agenda(events)
        return 0

    out = args.output or config.get("output", "calendar.ics")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(render(events, tzs, config.get("calendar_name", "課程行事曆")))
    print(f"✅ 寫入 {len(events)} 個事件到 {out}")
    # 全部來源都失敗時回傳錯誤碼，讓 GitHub Actions 變紅燈提醒你
    return 1 if errors and not events else 0


if __name__ == "__main__":
    sys.exit(main())
