#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
갭하락 출발 뒤 전일종가까지 올라온 자리에서
  A: 그 자리(전일종가 부근)에서 바로 매수
  B: 당일 시가로 지정가를 걸고 기다림
두 행동의 당일 종가 기준 성과를 과거 5분봉으로 비교한다. (사전 등록: PREREG_test.md)

입력: data/ 폴더의 야후 5분봉(yh5_<코드>.json)과 일봉(yd_<코드>.json), 코스피(KS11)
출력: 사건 목록 CSV, 요약 로그
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("wait_test")

import json
import math
import os
import random
import datetime as dt
from collections import OrderedDict, defaultdict

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else "data"
OUT_CSV = sys.argv[2] if len(sys.argv) > 2 else "wait_test_events.csv"
CHECKPOINTS = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]
AT_PC_BAND = 0.0025        # 전일종가 ±0.25% 안을 "전일종가 자리"로 본다
MIN_GAP = 0.003            # 갭하락 0.3% 이상만
ROUND_TRIP_COST = 0.0023   # 2026년 기준 매수수수료 0.015% + 매도수수료 0.015% + 거래세 0.20%
LAST_BAR_START = dt.time(14, 55)   # 야후 5분봉은 14:55봉(15:00 마감)까지만 있음
END_DATE_EXCL = dt.date(2026, 10, 1)   # 오늘은 결과가 안 끝났으므로 제외
PRIMARY = "000660"
PANEL = ["005930", "000660", "373220", "207940", "005380", "000270", "105560", "068270",
         "035420", "012450", "034020", "329180", "402340", "009150", "055550"]


def load_5m(code):
    path = os.path.join(DATA_DIR, f"yh5_{code}.json")
    j = json.load(open(path))["chart"]["result"][0]
    ts, q = j["timestamp"], j["indicators"]["quote"][0]
    days = OrderedDict()
    none_cnt = 0
    for i, t in enumerate(ts):
        k = dt.datetime.utcfromtimestamp(t) + dt.timedelta(hours=9)
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            none_cnt += 1
            continue
        days.setdefault(k.date(), []).append((k.time(), o, h, l, c))
    log.info(f"[LOAD5M] {code} | 일수 {len(days)} | 봉 {sum(len(v) for v in days.values())} | 값 없는 봉 제외 {none_cnt}")
    if none_cnt > 50:
        log.warning(f"[LOAD5M] {code} 값 없는 봉 {none_cnt}개 — 자료 점검 필요")
    return days


def load_1d(code):
    path = os.path.join(DATA_DIR, f"yd_{code}.json")
    j = json.load(open(path))["chart"]["result"][0]
    ts, q = j["timestamp"], j["indicators"]["quote"][0]
    rows = OrderedDict()
    for i, t in enumerate(ts):
        d = (dt.datetime.utcfromtimestamp(t) + dt.timedelta(hours=9)).date()
        if None in (q["open"][i], q["close"][i]):
            continue
        rows[d] = dict(open=q["open"][i], high=q["high"][i], low=q["low"][i], close_confirmed=q["close"][i])
    log.info(f"[LOAD1D] {code} | {len(rows)}일 | {next(iter(rows))} ~ {next(reversed(rows))}")
    return rows


def bar_ending_at(bars, hhmm):
    """hhmm 에 끝나는 5분봉(시작 = hhmm - 5분)의 종가. 그 시각에 화면에서 읽을 수 있는 값."""
    h, m = map(int, hhmm.split(":"))
    start = (dt.datetime(2000, 1, 1, h, m) - dt.timedelta(minutes=5)).time()
    for b in bars:
        if b[0] == start:
            return b
    return None


def find_events(code, d5, d1, ks5, ks1):
    events = []
    dates = [d for d in d5 if d < END_DATE_EXCL]
    daily_dates = list(d1.keys())
    skipped = defaultdict(int)
    for d in dates:
        if d not in d1:
            skipped["일봉없음"] += 1
            continue
        idx = daily_dates.index(d)
        if idx == 0:
            skipped["전일없음"] += 1
            continue
        pc = d1[daily_dates[idx - 1]]["close_confirmed"]
        day_open = d1[d]["open"]
        day_close = d1[d]["close_confirmed"]
        bars = d5[d]
        if abs(bars[0][1] - day_open) / day_open > 0.002:
            log.warning(f"[CHECK] {code} {d} 일봉 시가 {day_open:,.0f} vs 첫 5분봉 시가 {bars[0][1]:,.0f} 불일치")
        gap = day_open / pc - 1
        if gap > -MIN_GAP:
            skipped["갭하락아님"] += 1
            continue
        kbars = ks5.get(d)
        if not kbars or d not in ks1:
            skipped["지수없음"] += 1
            continue
        k_open = ks1[d]["open"]
        for cp in CHECKPOINTS:
            b = bar_ending_at(bars, cp)
            kb = bar_ending_at(kbars, cp)
            if b is None or kb is None:
                continue
            p_t = b[4]
            if abs(p_t / pc - 1) <= AT_PC_BAND:
                rs = (p_t / day_open - 1) - (kb[4] / k_open - 1)
                after = [x for x in bars if x[0] >= dt.time(int(cp[:2]), int(cp[3:])) and x[0] <= LAST_BAR_START]
                low_after = min(x[3] for x in after) if after else float("inf")
                high_after = max(x[2] for x in after) if after else float("nan")
                fill_through = low_after < day_open
                fill_touch = low_after <= day_open
                r_a = day_close / p_t - 1 - ROUND_TRIP_COST
                r_b = (day_close / day_open - 1 - ROUND_TRIP_COST) if fill_through else 0.0
                events.append(dict(code=code, date=d.isoformat(), t=cp, pc=pc, open=day_open, p_t=p_t,
                                   gap=gap, rs=rs, close=day_close, low_after=low_after, high_after=high_after,
                                   fill_through=fill_through, fill_touch=fill_touch, r_a=r_a, r_b=r_b))
                break
        else:
            skipped["전일종가자리없음"] += 1
    log.info(f"[EVENTS] {code} | 사건 {len(events)}건 | 제외 사유 {dict(skipped)}")
    return events


def summarize(tag, ev):
    n = len(ev)
    if n == 0:
        log.warning(f"[SUM] {tag} | 사건 0건")
        return None
    fill = [e for e in ev if e["fill_through"]]
    nofill = [e for e in ev if not e["fill_through"]]
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    diffs = [e["r_a"] - e["r_b"] for e in ev]
    md = mean(diffs)
    sd = math.sqrt(sum((x - md) ** 2 for x in diffs) / (n - 1)) if n > 1 else float("nan")
    t = md / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    p = math.erfc(abs(t) / math.sqrt(2)) if t == t else float("nan")   # 양측, 정규근사
    out = dict(tag=tag, n=n, fill_rate=len(fill) / n,
               mean_a=mean([e["r_a"] for e in ev]), mean_b=mean([e["r_b"] for e in ev]),
               mean_b_if_filled=mean([e["r_b"] for e in fill]),
               mean_a_when_b_filled=mean([e["r_a"] for e in fill]),
               mean_a_when_b_not_filled=mean([e["r_a"] for e in nofill]),
               win_a=mean([1.0 if e["r_a"] > 0 else 0.0 for e in ev]),
               mean_diff=md, t=t, p=p)
    log.info(f"[SUM] {tag} | n={n} | B체결률 {out['fill_rate']:.0%} | A평균 {out['mean_a']*100:+.2f}% | "
             f"B평균(미체결=0) {out['mean_b']*100:+.2f}% | B체결시 B {out['mean_b_if_filled']*100:+.2f}% | "
             f"B체결된 날 A {out['mean_a_when_b_filled']*100:+.2f}% | B미체결 날 A {out['mean_a_when_b_not_filled']*100:+.2f}% | "
             f"A-B {md*100:+.2f}%p t={t:.2f} p={p:.3f}")
    return out


def cluster_bootstrap(ev, reps=5000, seed=42):
    """날짜 단위로 묶어 재표집 — 같은 날 여러 종목이 같은 장세에 휘둘리는 것을 반영."""
    rnd = random.Random(seed)
    byday = defaultdict(list)
    for e in ev:
        byday[e["date"]].append(e["r_a"] - e["r_b"])
    days = list(byday)
    if len(days) < 3:
        return float("nan"), float("nan")
    stats = []
    for _ in range(reps):
        pick = [rnd.choice(days) for _ in days]
        vals = [v for d in pick for v in byday[d]]
        stats.append(sum(vals) / len(vals))
    stats.sort()
    return stats[int(0.025 * reps)], stats[int(0.975 * reps)]


def main():
    log.info(f"[MAIN] 입력 폴더 {DATA_DIR} | 점검 시각 {CHECKPOINTS} | 전일종가 밴드 ±{AT_PC_BAND:.2%} | 최소 갭 {MIN_GAP:.1%} | 비용 {ROUND_TRIP_COST:.2%}")
    ks5 = load_5m("KS11")
    ks1 = load_1d("KS11")
    all_ev = []
    for code in PANEL:
        d5 = load_5m(code)
        d1 = load_1d(code)
        ev = find_events(code, d5, d1, ks5, ks1)
        for e in ev:
            log.info(f"[EV] {code} {e['date']} T={e['t']} 갭 {e['gap']*100:+.2f}% RS {e['rs']*100:+.2f}%p | "
                     f"T가 {e['p_t']:,.0f} 시가 {e['open']:,.0f} T이후저가 {e['low_after']:,.0f} 종가 {e['close']:,.0f} | "
                     f"B체결 {'O' if e['fill_through'] else 'X'} | A {e['r_a']*100:+.2f}% B {e['r_b']*100:+.2f}%")
        all_ev.extend(ev)
    prim = [e for e in all_ev if e["code"] == PRIMARY]
    res = []
    res.append(summarize("SK하이닉스 전체", prim))
    res.append(summarize("SK하이닉스 RS>0", [e for e in prim if e["rs"] > 0]))
    res.append(summarize("대형주15 전체", all_ev))
    res.append(summarize("대형주15 RS>0", [e for e in all_ev if e["rs"] > 0]))
    res.append(summarize("대형주15 RS<=0", [e for e in all_ev if e["rs"] <= 0]))
    for tag, sub in [("대형주15 전체", all_ev), ("대형주15 RS>0", [e for e in all_ev if e["rs"] > 0])]:
        lo, hi = cluster_bootstrap(sub)
        log.info(f"[BOOT] {tag} | A-B 날짜묶음 부트스트랩 95% 구간 [{lo*100:+.2f}%p, {hi*100:+.2f}%p]")
    touch_rate = sum(1 for e in all_ev if e["fill_touch"]) / len(all_ev) if all_ev else float("nan")
    through_rate = sum(1 for e in all_ev if e["fill_through"]) / len(all_ev) if all_ev else float("nan")
    log.info(f"[CHECK] 대형주15 체결 판정 민감도 | 닿기만 {touch_rate:.0%} vs 관통 {through_rate:.0%}")
    with open(OUT_CSV, "w", encoding="utf-8-sig") as f:
        cols = list(all_ev[0].keys()) if all_ev else []
        f.write(",".join(cols) + "\n")
        for e in all_ev:
            f.write(",".join(str(e[c]) for c in cols) + "\n")
    log.info(f"[OUT] {OUT_CSV} {len(all_ev)}행 저장")


if __name__ == "__main__":
    main()
