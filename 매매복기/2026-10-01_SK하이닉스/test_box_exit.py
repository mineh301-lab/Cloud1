#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""탐색 갈래 X3: 박스 상단(T 이전 당일 고가) 재도달 시 청산하는 박스권 매매 방식으로 A·B 비교."""
import logging
import sys
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("box")
import datetime as dt
import test_wait_for_open as base
from test_extra_conditions import rs_at

COST = base.ROUND_TRIP_COST


def simulate(bars, t_start, entry_px, box_top, close_px):
    """t_start 이후 봉에서 box_top 이상 고가가 나오면 box_top에 청산, 아니면 종가 청산."""
    for b in bars:
        if b[0] < t_start or b[0] > base.LAST_BAR_START:
            continue
        if b[2] >= box_top:
            return box_top / entry_px - 1 - COST, True
    return close_px / entry_px - 1 - COST, False


def main():
    ks5 = base.load_5m("KS11"); ks1 = base.load_1d("KS11")
    out = []
    for code in base.PANEL:
        d5 = base.load_5m(code); d1 = base.load_1d(code)
        for e in base.find_events(code, d5, d1, ks5, ks1):
            d = dt.date.fromisoformat(e["date"])
            bars = d5[d]
            h, m = map(int, e["t"].split(":"))
            t = dt.time(h, m)
            box_top = max(b[2] for b in bars if b[0] < t)          # T 이전에 확정된 당일 고가
            ra, hit_a = simulate(bars, t, e["p_t"], box_top, e["close"])
            # B: T 이후 처음 시가 아래로 관통한 봉에서 체결, 그 다음 봉부터 박스 상단 재도달 확인
            fill_bar = next((b for b in bars if t <= b[0] <= base.LAST_BAR_START and b[3] < e["open"]), None)
            if fill_bar is None:
                rb, hit_b, filled = 0.0, False, False
            else:
                nxt = (dt.datetime(2000, 1, 1, fill_bar[0].hour, fill_bar[0].minute) + dt.timedelta(minutes=5)).time()
                rb, hit_b = simulate(bars, nxt, e["open"], box_top, e["close"])
                filled = True
            t30 = (dt.datetime(2000, 1, 1, h, m) - dt.timedelta(minutes=30)).strftime("%H:%M")
            rs30 = rs_at(bars, ks5[d], e["open"], ks1[d]["open"], t30)
            x1 = e["rs"] > 0 and rs30 is not None and rs30 > 0
            out.append(dict(code=code, date=e["date"], t=e["t"], box_top=box_top, room=box_top / e["p_t"] - 1,
                            ra=ra, hit_a=hit_a, rb=rb, hit_b=hit_b, filled=filled, x1=x1, rs=e["rs"]))
            log.info(f"[BOX] {code} {e['date']} T={e['t']} 박스상단 {box_top:,.0f}(+{(box_top/e['p_t']-1)*100:.2f}%) | "
                     f"A {ra*100:+.2f}% 도달{'O' if hit_a else 'X'} | B {'체결' if filled else '미체결'} {rb*100:+.2f}% | X1 {x1}")
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    for tag, sub in [("전체", out), ("X1 상대강도 30분 지속", [o for o in out if o["x1"]]), ("X1 아님", [o for o in out if not o["x1"]])]:
        n = len(sub)
        fil = [o for o in sub if o["filled"]]
        log.info(f"[BOXSUM] {tag} | n={n} | 박스상단까지 여유 평균 {mean([o['room'] for o in sub])*100:.2f}% | "
                 f"A 상단도달률 {mean([1.0 if o['hit_a'] else 0.0 for o in sub]):.0%} A평균 {mean([o['ra'] for o in sub])*100:+.2f}% | "
                 f"B 체결률 {len(fil)/n if n else float('nan'):.0%} B평균(미체결=0) {mean([o['rb'] for o in sub])*100:+.2f}% "
                 f"B체결분 {mean([o['rb'] for o in fil])*100:+.2f}% 체결 뒤 상단도달률 {mean([1.0 if o['hit_b'] else 0.0 for o in fil]):.0%}")


if __name__ == "__main__":
    main()
