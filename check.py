# -*- coding: utf-8 -*-
"""Naver domestic flight availability check -> ntfy push. Runs on GitHub Actions."""
import datetime
import json
import os
import sys
import urllib.request

DEP = "CJU"
ARR = "GMP"
DATE = "20260926"
ADULT = 2
FARE_TYPE = "YC"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
BOOK_URL = (
    f"https://flight.naver.com/flights/domestic/"
    f"{DEP}:airport-{ARR}:airport-{DATE}?adult={ADULT}&fareType={FARE_TYPE}"
)


def kst_today():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=9)).strftime("%Y%m%d")


def search_flights():
    body = json.dumps({
        "type": "domestic",
        "person": {"adult": ADULT, "child": 0, "infant": 0},
        "fareType": FARE_TYPE,
        "tripType": "OW",
        "itineraries": [{
            "departureAirport": DEP,
            "arrivalAirport": ARR,
            "departureDate": DATE,
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


def summarize(flights):
    rows = []
    best = None
    for f in flights:
        iid = f.get("itineraryId", "")
        time_str = f"{iid[8:10]}:{iid[10:12]}" if len(iid) >= 12 else "??:??"
        airline_flight = iid[18:] if len(iid) > 18 else "?"
        prices = [x.get("adultTotalFare") for x in f.get("fares", [])
                  if x.get("adultTotalFare")]
        low = min(prices) if prices else None
        if low is not None and (best is None or low < best):
            best = low
        seat = f.get("seatCount")
        rows.append((time_str, airline_flight, low, seat))
    rows.sort()
    lines = []
    for time_str, af, low, seat in rows[:8]:
        price_str = f"{low:,}원" if low else "가격미상"
        seat_str = f" 잔여{seat}석" if seat else ""
        lines.append(f"{time_str} {af} {price_str}{seat_str}")
    if len(rows) > 8:
        lines.append(f"... 외 {len(rows) - 8}편")
    return "\n".join(lines), best


def push(title, message):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set; skip push")
        return
    body = json.dumps({
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": 5,
        "tags": ["airplane"],
        "click": BOOK_URL,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def main():
    if kst_today() > DATE:
        print(f"departure date {DATE} passed; nothing to do")
        return
    if "--push-test" in sys.argv:
        push("[클라우드 테스트] 항공권 감시", "GitHub Actions에서 보낸 테스트 푸시입니다.")
        print("test push sent")
        return
    flights = search_flights()
    if not flights:
        print(f"{DATE} {DEP}->{ARR}: sold out (0 flights)")
        return
    summary, best = summarize(flights)
    best_str = f" 최저 {best:,}원/인" if best else ""
    print(f"{DATE} {DEP}->{ARR}: {len(flights)} flights found!{best_str}")
    print(summary)
    push(f"✈ 항공권 발견! 제주→김포 9/26{best_str}",
         summary + "\n\n알림을 누르면 예매 페이지로 이동")


if __name__ == "__main__":
    main()
