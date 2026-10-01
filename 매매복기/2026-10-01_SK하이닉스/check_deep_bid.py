# -*- coding: utf-8 -*-
"""강세 지속 사건(지금과 30분 전 모두 지수보다 강함) 가운데 장중에 전일종가 -2.5% 아래로 내려간 날 수.

1,732,000원(10/1 사용자님 대기 매수가)이 전일종가 1,776,000원 대비 -2.48% 자리라서 이 깊이를 잰다.
test_wait_for_open.py 의 사건 정의와 test_extra_conditions.py 의 상대강도 계산을 그대로 가져다 쓴다.
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("check_deep_bid")

import datetime as dt
import sys
import traceback

import test_extra_conditions as ex
import test_wait_for_open as base

DEPTH = 0.025


def main():
    ks5 = base.load_5m("KS11")
    ks1 = base.load_1d("KS11")
    n = hit = 0
    for code in base.PANEL:
        d5 = base.load_5m(code)
        for e in base.find_events(code, d5, base.load_1d(code), ks5, ks1):
            d = dt.date.fromisoformat(e["date"])
            tp = (dt.datetime.strptime(e["t"], "%H:%M") - dt.timedelta(minutes=30)).strftime("%H:%M")
            r_prev = ex.rs_at(d5[d], ks5[d], e["open"], ks1[d]["open"], tp)
            if e["rs"] > 0 and r_prev is not None and r_prev > 0:
                n += 1
                if e["low_after"] < e["pc"] * (1 - DEPTH):
                    hit += 1
                    log.info(f"[DEEP] {code} {e['date']} T={e['t']} 전일종가 {e['pc']:,.0f} T이후저가 {e['low_after']:,.0f}")
    if n == 0:
        log.warning("[DEEP] 강세 지속 사건 0건, 입력 확인 필요")
        return
    log.info(f"[SUM] 강세 지속 {n}건 중 전일종가 -{DEPTH:.1%} 아래 관통 {hit}건 ({hit / n:.0%})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"[FATAL] {type(e).__name__}: {e!r}\n{traceback.format_exc()}")
        sys.exit(1)
