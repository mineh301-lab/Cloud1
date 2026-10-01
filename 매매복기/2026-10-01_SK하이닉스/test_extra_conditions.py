#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""사전 등록 1차 검정의 추가 갈래(탐색): 상대강도 지속(X1)과 코스피 20분 이평 위(X2)."""
import logging
import sys
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("extra")
import datetime as dt
import test_wait_for_open as base


def rs_at(bars, kbars, day_open, k_open, hhmm):
    b = base.bar_ending_at(bars, hhmm)
    kb = base.bar_ending_at(kbars, hhmm)
    if b is None or kb is None:
        return None
    return (b[4] / day_open - 1) - (kb[4] / k_open - 1)


def kospi_above_ma(kbars, hhmm, n=4):
    h, m = map(int, hhmm.split(":"))
    end_start = (dt.datetime(2000, 1, 1, h, m) - dt.timedelta(minutes=5)).time()
    closes = [b[4] for b in kbars if b[0] <= end_start]
    if len(closes) < n:
        return None
    return closes[-1] > sum(closes[-n:]) / n


def main():
    ks5 = base.load_5m("KS11")
    ks1 = base.load_1d("KS11")
    rows = []
    for code in base.PANEL:
        d5 = base.load_5m(code)
        d1 = base.load_1d(code)
        for e in base.find_events(code, d5, d1, ks5, ks1):
            d = dt.date.fromisoformat(e["date"])
            h, m = map(int, e["t"].split(":"))
            t_minus30 = (dt.datetime(2000, 1, 1, h, m) - dt.timedelta(minutes=30)).strftime("%H:%M")
            rs30 = rs_at(d5[d], ks5[d], e["open"], ks1[d]["open"], t_minus30)
            e["x1"] = (e["rs"] > 0) and (rs30 is not None and rs30 > 0)
            e["x2"] = kospi_above_ma(ks5[d], e["t"]) is True
            rows.append(e)
    log.info(f"[EXTRA] 사건 {len(rows)}건 | X1 해당 {sum(r['x1'] for r in rows)} | X2 해당 {sum(r['x2'] for r in rows)} | 둘 다 {sum(r['x1'] and r['x2'] for r in rows)}")
    base.summarize("X1 상대강도 30분 지속", [r for r in rows if r["x1"]])
    base.summarize("X1 아님", [r for r in rows if not r["x1"]])
    base.summarize("X2 코스피 20분이평 위", [r for r in rows if r["x2"]])
    base.summarize("X2 아님", [r for r in rows if not r["x2"]])
    base.summarize("X1 그리고 X2", [r for r in rows if r["x1"] and r["x2"]])
    for tag, sub in [("X1", [r for r in rows if r["x1"]]), ("X1 그리고 X2", [r for r in rows if r["x1"] and r["x2"]])]:
        lo, hi = base.cluster_bootstrap(sub)
        log.info(f"[BOOT] {tag} | A-B 95% 구간 [{lo*100:+.2f}%p, {hi*100:+.2f}%p]")


if __name__ == "__main__":
    main()
