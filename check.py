# -*- coding: utf-8 -*-
"""Naver domestic flight availability check -> ntfy push. Runs on GitHub Actions."""
import datetime
import json
import os
import sys
import urllib.request

DEP = "CJU"
ARR = "GMP"
ADULT = 2
FARE_TYPE = "YC"
# (date YYYYMMDD, departure time from HHMM, to HHMM)
TARGETS = [
    ("20260926", "1600", "2359"),  # Sat after 16:00
    ("20260927", "0000", "0959"),  # Sun before 10:00
]
MAX_PRICE = 0  # per-adult total fare cap in KRW; 0 = no cap

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")


def book_url(date):
    return (f"https://flight.naver.com/flights/domestic/"
            f"{DEP}:airport-{ARR}:airport-{date}?adult={ADULT}&fareType={FARE_TYPE}")


def kst_today():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=9)).strftime("%Y%m%d")


def search_flights(date):
    body = json.dumps({
        "type": "domestic",
        "person": {"adult": ADULT, "child": 0, "infant": 0},
        "fareType": FARE_TYPE,
        "tripType": "OW",
        "itineraries": [{
            "departureAirport": DEP,
            "arrivalAirport": ARR,
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
    for date, f in found:
        iid = f.get("itineraryId", "")
        t = dep_time(f)
        airline_flight = iid[18:] if len(iid) > 18 else "?"
        low = lowest_fare(f)
        if low is not None and (best is None or low < best):
            best = low
        rows.append((date, t, airline_flight, low, f.get("seatCount")))
    rows.sort()
    lines = []
    for date, t, af, low, seat in rows[:8]:
        day = "토" if date == "20260926" else "일" if date == "20260927" else ""
        price_str = f"{low:,}원" if low else "가격미상"
        seat_str = f" 잔여{seat}석" if seat else ""
        lines.append(f"{date[4:6]}/{date[6:]}({day}) {t[:2]}:{t[2:]} {af} "
                     f"{price_str}{seat_str}")
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
    if kst_today() > TARGETS[-1][0]:
        print("all departure dates passed; nothing to do")
        return
    if "--push-test" in sys.argv:
        push("[클라우드 테스트] 항공권 감시",
             "GitHub Actions에서 보낸 테스트 푸시입니다.", book_url(TARGETS[0][0]))
        print("test push sent")
        return

    found = []
    for date, t_from, t_to in TARGETS:
        flights = search_flights(date)
        matched = [f for f in flights if t_from <= dep_time(f) <= t_to]
        if MAX_PRICE:
            matched = [f for f in matched
                       if (lowest_fare(f) or 10 ** 9) <= MAX_PRICE]
        print(f"{date} {DEP}->{ARR} {t_from}-{t_to}: "
              f"{len(flights)} total, {len(matched)} matched")
        found.extend((date, f) for f in matched)

    if not found:
        print("no matching seats")
        return
    summary, best = summarize(found)
    best_str = f" 최저 {best:,}원/인" if best else ""
    print(f"MATCH! {len(found)} flights{best_str}")
    print(summary)
    push(f"✈ 항공권 발견! 제주→김포 (토16시↑/일10시↓){best_str}",
         summary + "\n\n알림을 누르면 예매 페이지로 이동",
         book_url(found[0][0]))


if __name__ == "__main__":
    main()
