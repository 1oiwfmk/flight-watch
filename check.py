# -*- coding: utf-8 -*-
"""Naver domestic flight availability check -> ntfy push. Runs on GitHub Actions."""
import datetime
import json
import os
import sys
import urllib.request

PARTY = 3            # travel party size (booking link)
FARE_TYPE = "YC"
# (departure airport, arrival airport, date YYYYMMDD, dep time from HHMM, to HHMM)
TARGETS = [
    ("GMP", "CJU", "20260924", "0000", "2359"),  # Thu all day
    ("CJU", "GMP", "20260926", "1600", "2359"),  # Sat after 16:00
    ("CJU", "GMP", "20260927", "0000", "1129"),  # Sun before 11:30
]
MAX_PRICE = 0  # per-adult total fare cap in KRW; 0 = no cap

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = "state.json"      # persisted between runs via actions/cache
RENOTIFY_MINUTES = 60          # re-push interval while seats stay available
AIRPORT_KR = {"GMP": "김포", "CJU": "제주"}
DAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def book_url(dep, arr, date, adult=PARTY):
    return (f"https://flight.naver.com/flights/domestic/"
            f"{dep}:airport-{arr}:airport-{date}?adult={adult}&fareType={FARE_TYPE}")


def seat_count(f):
    return f.get("seatCount")


def day_label(date):
    try:
        return DAY_KR[datetime.datetime.strptime(date, "%Y%m%d").weekday()]
    except ValueError:
        return ""


def kst_today():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=9)).strftime("%Y%m%d")


def search_variant():
    """Rotate adult/infant combos so each run hits a different Naver
    cache key (~15 min TTL) and triggers a fresh search more often.
    Adults 1-2 only: flights with >=2 seats stay visible in every combo."""
    slot = (datetime.datetime.now(datetime.timezone.utc).minute // 2) % 4
    return [(1, 0), (1, 1), (2, 0), (2, 1)][slot]


def search_flights(dep, arr, date):
    adult, infant = search_variant()
    body = json.dumps({
        "type": "domestic",
        "person": {"adult": adult, "child": 0, "infant": infant},
        "fareType": FARE_TYPE,
        "tripType": "OW",
        "itineraries": [{
            "departureAirport": dep,
            "arrivalAirport": arr,
            "departureDate": date,
        }],
        "device": "PC",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://flight-api.naver.com/flight/domestic/searchFlights",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/127 Safari/537.36",
            "Referer": "https://flight.naver.com/",
        },
        method="POST",
    )
    raw = urllib.request.urlopen(req, timeout=90).read().decode("utf-8")
    flights = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        for f in event.get("flights", []):
            fid = f.get("itineraryId", "")
            if fid:
                flights[fid] = f
    return list(flights.values())


def dep_time(f):
    iid = f.get("itineraryId", "")
    return iid[8:12] if len(iid) >= 12 else "0000"


def lowest_fare(f):
    prices = [x.get("adultTotalFare") for x in f.get("fares", [])
              if x.get("adultTotalFare")]
    return min(prices) if prices else None


def summarize(found):
    rows = []
    best = None
    for dep, arr, date, f in found:
        iid = f.get("itineraryId", "")
        t = dep_time(f)
        airline_flight = iid[18:] if len(iid) > 18 else "?"
        low = lowest_fare(f)
        if low is not None and (best is None or low < best):
            best = low
        rows.append((date, t, dep, arr, airline_flight, low, f.get("seatCount")))
    rows.sort()
    lines = []
    for date, t, dep, arr, af, low, seat in rows[:8]:
        route = f"{AIRPORT_KR.get(dep, dep)}→{AIRPORT_KR.get(arr, arr)}"
        price_str = f"{low:,}원" if low else "가격미상"
        seat_str = f" 잔여{seat}석" if seat else ""
        lines.append(f"{date[4:6]}/{date[6:]}({day_label(date)}) {t[:2]}:{t[2:]} "
                     f"{route} {af} {price_str}{seat_str}")
    if len(rows) > 8:
        lines.append(f"... 외 {len(rows) - 8}편")
    return "\n".join(lines), best


def push(title, message, click_url):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set; skip push")
        return
    body = json.dumps({
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": 5,
        "tags": ["airplane"],
        "click": click_url,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def main():
    today = kst_today()
    active = [t for t in TARGETS if t[2] >= today]
    if not active:
        print("all departure dates passed; nothing to do")
        return
    if "--push-test" in sys.argv:
        push("[클라우드 테스트] 항공권 감시",
             "GitHub Actions에서 보낸 테스트 푸시입니다.",
             book_url(*active[0][:3]))
        print("test push sent")
        return

    found = []
    for dep, arr, date, t_from, t_to in active:
        flights = search_flights(dep, arr, date)
        matched = [f for f in flights if t_from <= dep_time(f) <= t_to]
        if MAX_PRICE:
            matched = [f for f in matched
                       if (lowest_fare(f) or 10 ** 9) <= MAX_PRICE]
        print(f"{date} {dep}->{arr} {t_from}-{t_to}: "
              f"{len(flights)} total, {len(matched)} matched")
        found.extend((dep, arr, date, f) for f in matched)

    state = {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        pass

    # alert only when some flight has >=2 seats (or unknown seat count);
    # 1-seat flights stay in the summary as +1 split-booking candidates
    trigger = [x for x in found
               if seat_count(x[3]) is None or seat_count(x[3]) >= 2]
    if not trigger:
        print("no matching seats (>=2)")
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"available": False}, fh)
        return

    three_ok = any(seat_count(x[3]) is None or seat_count(x[3]) >= PARTY
                   for x in found)
    summary, best = summarize(found)
    best_str = f" 최저 {best:,}원/인" if best else ""
    print(f"MATCH! {len(trigger)} flights{best_str}")
    print(summary)

    now = datetime.datetime.now(datetime.timezone.utc)
    if state.get("available") and state.get("last_notified"):
        try:
            elapsed = (now - datetime.datetime.fromisoformat(
                state["last_notified"])).total_seconds()
            if elapsed < RENOTIFY_MINUTES * 60:
                print(f"suppressed (notified {int(elapsed // 60)}m ago)")
                return
        except ValueError:
            pass

    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"available": True, "last_notified": now.isoformat()}, fh)
    kind = " — 3인 한번에 가능!" if three_ok else " — 2석 발견(분할예매 검토)"
    push(f"✈ 항공권 발견!{kind}{best_str}",
         summary + "\n\n알림을 누르면 예매 페이지로 이동",
         book_url(*trigger[0][:3], PARTY if three_ok else 2))


if __name__ == "__main__":
    main()
