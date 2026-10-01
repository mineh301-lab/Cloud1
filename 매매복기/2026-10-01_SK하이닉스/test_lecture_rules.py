#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
강의 조항 6개(L1~L6)를 test_wait_for_open.py 의 72건 사건에 붙여,
조항대로 한 경우와 아닌 경우의 당일 종가 성과를 비교한다.
사전 등록: PREREG_test.md 「강의 조항별 검정」 절 (계산 전 고정).

입력: data/ 의 야후 5분봉과 일봉, data/flow/ 의 외국인 일별 순매수(fetch_flow.py),
      data/month_fills_000660.json(사용자님 9/8~9/28 하이닉스 체결), data/min_*_20261001.json(10/1 1분봉)
출력: lecture_test_log.txt 로 리다이렉트해서 보관, lecture_test_events.csv
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("lecture")

import csv
import datetime as dt
import json
import math
import os
import random
import traceback
from collections import defaultdict

import test_extra_conditions as ex
import test_wait_for_open as base

COST = base.ROUND_TRIP_COST
DEEP = 0.975            # 10/1 실제 대기 주문 1,732,000 = 전일종가 1,776,000 x 0.9752
REPS = 5000
N_FAMILY = 6            # L1~L6
ALPHA_FAMILY = 0.05 / N_FAMILY
FLOW_DIR = os.path.join(base.DATA_DIR, "flow")
OUT_CSV = "lecture_test_events.csv"


# ---------------------------------------------------------------- 자료 붙이기
def load_flow(code):
    path = os.path.join(FLOW_DIR, f"trend_{code}.json")
    rows = json.load(open(path, encoding="utf-8"))
    out = {}
    for r in rows:
        d = dt.date(int(r["bizdate"][:4]), int(r["bizdate"][4:6]), int(r["bizdate"][6:]))
        out[d] = int(r["foreignerPureBuyQuant"].replace(",", "").replace("+", ""))
    log.info(f"[FLOW] {code} {len(out)}일 {min(out)}~{max(out)}")
    return out


def prior_flow(flow, d, n=5):
    days = sorted(x for x in flow if x < d)[-n:]
    if len(days) < n:
        return None
    return sum(flow[x] for x in days)


def enrich(e, bars, kbars, k_open, flow):
    d = dt.date.fromisoformat(e["date"])
    h, m = map(int, e["t"].split(":"))
    t_time = dt.time(h, m)
    pc, op, pt, close, low_after = e["pc"], e["open"], e["p_t"], e["close"], e["low_after"]
    before = [b for b in bars if b[0] < t_time]          # T에 끝난 점검봉까지 포함
    if not before:
        raise ValueError(f"{e['code']} {e['date']} T 이전 봉 없음")
    high_before = max(b[2] for b in before)
    touches = sum(1 for i in range(1, len(before)) if before[i - 1][4] > pc and before[i][3] <= pc)
    t30 = (dt.datetime(2000, 1, 1, h, m) - dt.timedelta(minutes=30)).strftime("%H:%M")
    rs30 = ex.rs_at(bars, kbars, op, k_open, t30)
    above = ex.kospi_above_ma(kbars, e["t"])
    pf = prior_flow(flow, d)

    def leg(level, marketable_at=None):
        if marketable_at is not None and marketable_at <= level:
            return close / marketable_at - 1 - COST, True
        if low_after < level:
            return close / level - 1 - COST, True
        return 0.0, False

    r_pc, f_pc = leg(pc, marketable_at=pt)
    r_op, f_op = leg(op)
    r_dp, f_dp = leg(pc * DEEP)
    # 탐색 E2: T 이후 T 이전 당일 고가를 처음 넘는 봉에서 그 고가로 매수(봉 시가가 이미 위면 봉 시가)
    bo_px = None
    for b in bars:
        if t_time <= b[0] <= base.LAST_BAR_START and b[2] > high_before:
            bo_px = max(high_before, b[1])
            break
    r_bo = close / bo_px - 1 - COST if bo_px else 0.0
    e.update(
        bo_filled=bo_px is not None,
        r_bo=r_bo,
        r_wb=0.5 * e["r_b"] + 0.5 * r_bo,
        rs_pos=e["rs"] > 0,
        x1=(e["rs"] > 0) and (rs30 is not None and rs30 > 0),
        kospi_above=above,
        high_before=high_before,
        break_high=e["high_after"] > high_before,
        revisit_open=e["fill_through"],
        touches=touches,
        flow5=pf,
        ladder=(r_pc + r_op + r_dp) / 3,
        legs=int(f_pc) + int(f_op) + int(f_dp),
        legs_all=f_pc and f_op and f_dp,
        move_after=close / pt - 1,
        r_u=r_dp, u_filled=f_dp,
        r_w=e["r_b"], w_filled=e["fill_through"],
        r_h=0.5 * e["r_a"] + 0.5 * e["r_b"],
    )
    return e


# ---------------------------------------------------------------- 통계
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def _p_and_ci(obs, stats):
    stats = sorted(s for s in stats if s == s)
    if len(stats) < 100:
        return float("nan"), (float("nan"), float("nan")), float("nan")
    k = len(stats)
    lo, hi = stats[int(0.025 * k)], stats[int(0.975 * k) - 1]
    le = sum(1 for s in stats if s <= 0) / k
    ge = sum(1 for s in stats if s >= 0) / k
    p = min(1.0, 2 * min(le, ge))
    mu = mean(stats)
    se = math.sqrt(sum((s - mu) ** 2 for s in stats) / (k - 1))
    return p, (lo, hi), se


def boot_groups(ev, in_g1, val, seed=7):
    """두 집단 평균 차(g1 - g0). 날짜 단위 재표집."""
    byday = defaultdict(list)
    for e in ev:
        byday[e["date"]].append(e)
    days = list(byday)
    g1 = [val(e) for e in ev if in_g1(e)]
    g0 = [val(e) for e in ev if not in_g1(e)]
    obs = mean(g1) - mean(g0)
    rnd = random.Random(seed)
    stats = []
    for _ in range(REPS):
        pick = [x for dd in (rnd.choice(days) for _ in days) for x in byday[dd]]
        a = [val(e) for e in pick if in_g1(e)]
        b = [val(e) for e in pick if not in_g1(e)]
        if a and b:
            stats.append(mean(a) - mean(b))
    p, ci, se = _p_and_ci(obs, stats)
    return dict(n1=len(g1), n0=len(g0), m1=mean(g1), m0=mean(g0), diff=obs, p=p, ci=ci, se=se, mde=2.8 * se)


def boot_paired(ev, dval, seed=11):
    byday = defaultdict(list)
    for e in ev:
        byday[e["date"]].append(dval(e))
    days = list(byday)
    obs = mean(dval(e) for e in ev)
    rnd = random.Random(seed)
    stats = []
    for _ in range(REPS):
        vals = [v for dd in (rnd.choice(days) for _ in days) for v in byday[dd]]
        stats.append(mean(vals))
    p, ci, se = _p_and_ci(obs, stats)
    return dict(n=len(ev), diff=obs, p=p, ci=ci, se=se, mde=2.8 * se)


def within_day(ev, in_g1, val):
    """같은 날 두 집단이 모두 있는 날만, 날짜 안 평균 차를 다시 평균."""
    byday = defaultdict(list)
    for e in ev:
        byday[e["date"]].append(e)
    diffs = []
    for dd, xs in byday.items():
        a = [val(e) for e in xs if in_g1(e)]
        b = [val(e) for e in xs if not in_g1(e)]
        if a and b:
            diffs.append(mean(a) - mean(b))
    return len(diffs), mean(diffs)


def fmt_pct(x):
    return f"{x * 100:+.2f}%"


def report(tag, r, unit="%p"):
    sig = "본페로니 통과" if r["p"] == r["p"] and r["p"] < ALPHA_FAMILY else "본페로니 미통과"
    if "n1" in r:
        head = f"n {r['n1']} 대 {r['n0']} | 평균 {r['m1'] * 100:+.2f} 대 {r['m0'] * 100:+.2f}"
    else:
        head = f"n {r['n']}"
    log.info(f"[TEST] {tag} | {head} | 차이 {r['diff'] * 100:+.2f}{unit} | 95% 구간 [{r['ci'][0] * 100:+.2f}, {r['ci'][1] * 100:+.2f}] | "
             f"p={r['p']:.4f} ({sig}, 기준 {ALPHA_FAMILY:.4f}) | 최소탐지효과 {r['mde'] * 100:.2f}{unit}")


# ---------------------------------------------------------------- 사용자님 본인 체결 (판례 18)
def user_fills_check(d5, d1, ks5, ks1):
    path = os.path.join(base.DATA_DIR, "month_fills_000660.json")
    fills = json.load(open(path, encoding="utf-8"))
    rows = []
    skipped = defaultdict(int)
    for f in fills:
        dstr, tstr, side, q, px = f[0], f[1], f[2], f[3], f[4]
        if side != "매수":
            continue
        d = dt.date.fromisoformat(dstr)
        t = dt.datetime.strptime(tstr, "%H:%M:%S").time()
        if not (dt.time(9, 0) <= t < dt.time(15, 20)):
            skipped["정규장 연속매매 밖"] += 1
            continue
        if d not in d5 or d not in ks5 or d not in d1 or d not in ks1:
            skipped["시세 없음"] += 1
            continue
        cut = (dt.datetime.combine(d, t) - dt.timedelta(minutes=5)).time()
        hb = [b for b in d5[d] if b[0] <= cut]
        kb = [b for b in ks5[d] if b[0] <= cut]
        if len(hb) < 1 or len(kb) < 4:
            skipped["체결 전 완성봉 부족"] += 1
            continue
        op, kop = d1[d]["open"], ks1[d]["open"]
        rs = (hb[-1][4] / op - 1) - (kb[-1][4] / kop - 1)
        above = kb[-1][4] > sum(b[4] for b in kb[-4:]) / 4
        close = d1[d]["close_confirmed"]
        rows.append(dict(date=dstr, t=tstr, q=q, px=px, rs=rs, above=above, above_open=px > op,
                         ret=close / px - 1))
        log.info(f"[UFILL] {dstr} {tstr} {q}@{px:,} | RS {rs * 100:+.2f}%p | 코스피 이평 {'위' if above else '아래'} | "
                 f"시가 {op:,.0f} {'위' if px > op else '아래'} | 당일종가 {close:,.0f} | {(close / px - 1) * 100:+.2f}%")
    log.info(f"[UFILL] 정규장 매수 체결 {len(rows)}건 | 제외 {dict(skipped)}")
    if len(rows) < 10:
        log.warning("[UFILL] 체결 10건 미만, 비율 해석 금지")
    for tag, key in [("코스피 이평 위", "above"), ("지수 대비 강세(RS>0)", "rs_pos"), ("시가 위 체결", "above_open")]:
        g1 = [r for r in rows if (r[key] if key != "rs_pos" else r["rs"] > 0)]
        g0 = [r for r in rows if not (r[key] if key != "rs_pos" else r["rs"] > 0)]
        log.info(f"[USUM] {tag} {len(g1)}건 평균 {mean(r['ret'] for r in g1) * 100:+.2f}% 종가가 위 {mean(1.0 if r['ret'] > 0 else 0.0 for r in g1):.0%} | "
                 f"아닌 쪽 {len(g0)}건 평균 {mean(r['ret'] for r in g0) * 100:+.2f}% 종가가 위 {mean(1.0 if r['ret'] > 0 else 0.0 for r in g0):.0%} (비용 전)")
    return rows


# ---------------------------------------------------------------- 10/1 값 (같은 정의로)
def today_values():
    def load(name):
        raw = json.load(open(os.path.join(base.DATA_DIR, f"min_{name}_20261001.json"), encoding="utf-8"))
        out = []
        for r in raw:
            s = r["localDateTime"]
            if s[:8] != "20261001":
                continue
            t = dt.time(int(s[8:10]), int(s[10:12]))
            if dt.time(9, 0) <= t < dt.time(15, 20):
                out.append((t, r["openPrice"], r["highPrice"], r["lowPrice"], r["currentPrice"]))
        return out

    def to5(rows):
        agg = {}
        for t, o, h, l, c in rows:
            k = dt.time(t.hour, t.minute - t.minute % 5)
            if k not in agg:
                agg[k] = [k, o, h, l, c]
            else:
                a = agg[k]
                a[2], a[3], a[4] = max(a[2], h), min(a[3], l), c
        return [tuple(v) for _, v in sorted(agg.items())]

    hy, ks = to5(load("000660")), to5(load("KOSPI"))
    pc, op, kop = 1776000.0, hy[0][1], ks[0][1]
    flow = load_flow("000660")
    for cp in ["10:30", "11:00"]:
        h, m = map(int, cp.split(":"))
        tt = dt.time(h, m)
        before = [b for b in hy if b[0] < tt]
        kb = [b for b in ks if b[0] < tt]
        touches = sum(1 for i in range(1, len(before)) if before[i - 1][4] > pc and before[i][3] <= pc)
        rs = (before[-1][4] / op - 1) - (kb[-1][4] / kop - 1)
        above = kb[-1][4] > sum(b[4] for b in kb[-4:]) / 4
        hb = max(b[2] for b in before)
        after = [b for b in hy if b[0] >= tt]
        bo = next((b for b in after if b[2] > hb), None)
        log.info(f"[TODAY] 10/1 {cp} | 하닉 {before[-1][4]:,.0f} 시가 {op:,.0f} | RS {rs * 100:+.2f}%p | 코스피 5분봉 4개 이평 {'위' if above else '아래'} | "
                 f"5분봉 기준 위에서 전일종가 터치 {touches}봉 | 당일 고가(그때까지) {hb:,.0f} | "
                 f"그 뒤 첫 돌파 봉 {bo[0].strftime('%H:%M') + ' 고가 ' + format(bo[2], ',.0f') + ' 시가 ' + format(bo[1], ',.0f') if bo else '없음'} | "
                 f"그 뒤 최저 {min(b[3] for b in after):,.0f} (자료 끝 {hy[-1][0].strftime('%H:%M')}봉)")
    log.info(f"[TODAY] 10/1 직전 5거래일 외국인 순매수 합 {prior_flow(flow, dt.date(2026, 10, 1)):+,}주")


# ---------------------------------------------------------------- 본체
def main():
    log.info(f"[MAIN] 비용 {COST:.2%} | 깊은 지정가 전일종가 x {DEEP} | 부트스트랩 {REPS}회 | 묶음 {N_FAMILY}개, 본페로니 기준 {ALPHA_FAMILY:.4f}")
    ks5 = base.load_5m("KS11")
    ks1 = base.load_1d("KS11")
    ev = []
    hy5 = hy1 = None
    for code in base.PANEL:
        d5 = base.load_5m(code)
        d1 = base.load_1d(code)
        flow = load_flow(code)
        if code == "000660":
            hy5, hy1 = d5, d1
        for e in base.find_events(code, d5, d1, ks5, ks1):
            d = dt.date.fromisoformat(e["date"])
            ev.append(enrich(e, d5[d], ks5[d], ks1[d]["open"], flow))
    log.info(f"[EVENTS] {len(ev)}건 | RS>0 {sum(e['rs_pos'] for e in ev)} | 30분 강세 지속 {sum(e['x1'] for e in ev)} | "
             f"코스피 이평 위 {sum(1 for e in ev if e['kospi_above'])} | 외국인 5일 순매수 0 이상 {sum(1 for e in ev if e['flow5'] is not None and e['flow5'] >= 0)} | "
             f"터치 3봉 이상 {sum(1 for e in ev if e['touches'] >= 3)} | 날짜 {len(set(e['date'] for e in ev))}일")
    if len(ev) != 72:
        log.warning(f"[EVENTS] 사건 수 {len(ev)} 가 이전 실행(72)과 다름, 자료 변경 확인 필요")
    missing_flow = [e for e in ev if e["flow5"] is None]
    if missing_flow:
        log.warning(f"[EVENTS] 외국인 순매수 5일 합 없음 {len(missing_flow)}건")

    pos = [e for e in ev if e["rs_pos"]]
    neg = [e for e in ev if not e["rs_pos"]]

    # L1
    log.info("==== L1 4강 대장주 정의: 지수보다 강한 종목은 하락 폭을 금방 회복하는가")
    r = boot_groups(ev, lambda e: not e["rs_pos"], lambda e: 1.0 if e["revisit_open"] else 0.0)
    report("L1 주 | 시가 아래 재하락 비율, RS<=0 빼기 RS>0", r)
    r2 = boot_groups(ev, lambda e: e["rs_pos"], lambda e: 1.0 if e["break_high"] else 0.0)
    report("L1 보조 | T 이전 당일 고가 돌파 비율, RS>0 빼기 RS<=0", r2)
    nd, wd = within_day(ev, lambda e: not e["rs_pos"], lambda e: 1.0 if e["revisit_open"] else 0.0)
    log.info(f"[TEST] L1 보조 | 같은 날 안 비교(두 집단 다 있는 날 {nd}일) 재하락 비율 차 {wd * 100:+.1f}%p")
    for tag, sub in [("RS>0", pos), ("RS<=0", neg), ("30분 강세 지속", [e for e in ev if e["x1"]])]:
        log.info(f"[DESC] L1 {tag} {len(sub)}건 | 시가 재하락 {mean(1.0 if e['revisit_open'] else 0.0 for e in sub):.0%} | "
                 f"당일 고가 돌파 {mean(1.0 if e['break_high'] else 0.0 for e in sub):.0%} | 전일종가 x0.975 관통 {mean(1.0 if e['u_filled'] else 0.0 for e in sub):.0%} | "
                 f"T가격 대비 종가 평균 {mean(e['move_after'] for e in sub) * 100:+.2f}%")

    # L2
    log.info("==== L2 10강 TWAP 절: 여러 가격대 사다리는 강한 종목에서 덜 체결되고 약한 종목에서 전량 체결 후 손실인가")
    r = boot_groups(ev, lambda e: e["rs_pos"], lambda e: e["ladder"])
    report("L2 주 | 사다리 평균 성과, RS>0 빼기 RS<=0", r)
    for tag, sub in [("RS>0", pos), ("RS<=0", neg)]:
        allf = [e for e in sub if e["legs_all"]]
        few = [e for e in sub if e["legs"] <= 1]
        log.info(f"[DESC] L2 {tag} {len(sub)}건 | 평균 체결 다리 {mean(e['legs'] for e in sub):.2f}/3 | 세 다리 모두 {len(allf)}건({len(allf) / len(sub):.0%}) "
                 f"그때 사다리 {mean(e['ladder'] for e in allf) * 100:+.2f}% | 한 다리 이하 {len(few)}건 그날 T가격 대비 종가 {mean(e['move_after'] for e in few) * 100:+.2f}% | "
                 f"사다리 평균 {mean(e['ladder'] for e in sub) * 100:+.2f}%")

    # L3
    log.info("==== L3 10강 주문 집행 반반 + 일지 '일부만': RS>0 사건에서 반은 실시간, 반은 시가 대기 대 전부 깊은 대기")
    r = boot_paired(pos, lambda e: e["r_h"] - e["r_u"])
    report("L3 주 | H 빼기 U (RS>0)", r)
    r2 = boot_paired(pos, lambda e: e["r_h"] - e["r_w"])
    report("L3 보조 | H 빼기 W (RS>0)", r2)
    r3 = boot_paired(pos, lambda e: e["r_w"] - e["r_u"])
    report("L3 보조 | W 빼기 U (RS>0)", r3)
    x1 = [e for e in pos if e["x1"]]
    r4 = boot_paired(x1, lambda e: e["r_h"] - e["r_u"])
    report("L3 민감도 | H 빼기 U (30분 강세 지속)", r4)
    for tag, sub in [("RS>0", pos), ("30분 강세 지속", x1), ("RS<=0", neg)]:
        log.info(f"[DESC] L3 {tag} {len(sub)}건 | 평균 A {fmt_pct(mean(e['r_a'] for e in sub))} H {fmt_pct(mean(e['r_h'] for e in sub))} "
                 f"W {fmt_pct(mean(e['r_w'] for e in sub))} U {fmt_pct(mean(e['r_u'] for e in sub))} | "
                 f"0주로 끝나고 종가가 T가격 위: W {mean(1.0 if (not e['w_filled'] and e['move_after'] > 0) else 0.0 for e in sub):.0%} "
                 f"U {mean(1.0 if (not e['u_filled'] and e['move_after'] > 0) else 0.0 for e in sub):.0%} H 0% | "
                 f"전부 체결 뒤 손실: A {mean(1.0 if e['r_a'] < 0 else 0.0 for e in sub):.0%} "
                 f"H {mean(1.0 if (e['w_filled'] and e['r_h'] < 0) else 0.0 for e in sub):.0%} "
                 f"W {mean(1.0 if (e['w_filled'] and e['r_w'] < 0) else 0.0 for e in sub):.0%} "
                 f"U {mean(1.0 if (e['u_filled'] and e['r_u'] < 0) else 0.0 for e in sub):.0%}")

    # L4
    log.info("==== L4 10강 시장분위기: 코스피 20분 이평 위에서 산 쪽이 나았는가")
    sub = [e for e in ev if e["kospi_above"] is not None]
    r = boot_groups(sub, lambda e: e["kospi_above"], lambda e: e["r_a"])
    report("L4 주 | A 성과, 이평 위 빼기 아래", r)
    r2 = boot_groups(sub, lambda e: e["kospi_above"], lambda e: e["r_b"])
    report("L4 보조 | W(시가 대기) 성과, 이평 위 빼기 아래", r2)
    nd, wd = within_day(sub, lambda e: e["kospi_above"], lambda e: e["r_a"])
    log.info(f"[TEST] L4 보조 | 같은 날 안 비교(두 집단 다 있는 날 {nd}일) A 성과 차 {wd * 100:+.2f}%p")

    # L5
    log.info("==== L5 8강 대전제: 직전 며칠 외국인 순매수(지금까지의 수급)와 그 시각 지수 대비 강도(대장주 판단) 중 무엇이 성과를 갈랐나")
    sub = [e for e in ev if e["flow5"] is not None]
    r = boot_groups(sub, lambda e: e["flow5"] >= 0, lambda e: e["r_a"])
    report("L5 주 | A 성과, 외국인 5일 순매수 0 이상 빼기 음수", r)
    r2 = boot_groups(sub, lambda e: e["rs_pos"], lambda e: e["r_a"])
    report("L5 나란히 | A 성과, RS>0 빼기 RS<=0 (같은 표본)", r2)
    for tag, s2 in [("외국인 음수 그리고 RS>0", [e for e in sub if e["flow5"] < 0 and e["rs_pos"]]),
                    ("외국인 음수 그리고 RS<=0", [e for e in sub if e["flow5"] < 0 and not e["rs_pos"]]),
                    ("외국인 0 이상 그리고 RS>0", [e for e in sub if e["flow5"] >= 0 and e["rs_pos"]]),
                    ("외국인 0 이상 그리고 RS<=0", [e for e in sub if e["flow5"] >= 0 and not e["rs_pos"]])]:
        if s2:
            log.info(f"[DESC] L5 {tag} {len(s2)}건 | A {fmt_pct(mean(e['r_a'] for e in s2))} W {fmt_pct(mean(e['r_w'] for e in s2))} "
                     f"U {fmt_pct(mean(e['r_u'] for e in s2))} H {fmt_pct(mean(e['r_h'] for e in s2))} | 시가 재하락 {mean(1.0 if e['revisit_open'] else 0.0 for e in s2):.0%}")

    # L6
    log.info("==== L6 7강 주의점 (7): 전일종가를 위에서 자주 건드린 뒤 그 자리에서 산 쪽이 나빴나 (전제 불성립, 참고)")
    r = boot_groups(ev, lambda e: e["touches"] < 3, lambda e: e["r_a"])
    report("L6 주 | A 성과, 터치 3봉 미만 빼기 3봉 이상", r)
    for tag, s2 in [("터치 3봉 미만", [e for e in ev if e["touches"] < 3]), ("터치 3봉 이상", [e for e in ev if e["touches"] >= 3])]:
        log.info(f"[DESC] L6 {tag} {len(s2)}건 | A {fmt_pct(mean(e['r_a'] for e in s2))} W {fmt_pct(mean(e['r_w'] for e in s2))} | "
                 f"시가 재하락 {mean(1.0 if e['revisit_open'] else 0.0 for e in s2):.0%} | RS>0 비율 {mean(1.0 if e['rs_pos'] else 0.0 for e in s2):.0%}")

    # 탐색 E1, E2 (결과를 본 뒤 추가, PREREG 끝 절)
    log.info("==== 탐색 E1: 4강(지수보다 강함)과 7강 주의점 (7)(전일종가 잦은 터치)이 엇갈린 칸")
    def cell(tag, s2):
        if not s2:
            log.info(f"[DESC] E {tag} 0건")
            return
        log.info(f"[DESC] E {tag} {len(s2)}건 | 시가 재하락 {mean(1.0 if e['revisit_open'] else 0.0 for e in s2):.0%} | "
                 f"A {fmt_pct(mean(e['r_a'] for e in s2))} W {fmt_pct(mean(e['r_w'] for e in s2))} U {fmt_pct(mean(e['r_u'] for e in s2))} "
                 f"H {fmt_pct(mean(e['r_h'] for e in s2))} BO {fmt_pct(mean(e['r_bo'] for e in s2))} WB {fmt_pct(mean(e['r_wb'] for e in s2))} | "
                 f"돌파 발생 {mean(1.0 if e['bo_filled'] else 0.0 for e in s2):.0%}")
    cell("RS>0 그리고 터치 3봉 이상", [e for e in pos if e["touches"] >= 3])
    cell("RS>0 그리고 터치 3봉 미만", [e for e in pos if e["touches"] < 3])
    cell("RS<=0 그리고 터치 3봉 이상", [e for e in neg if e["touches"] >= 3])
    cell("RS<=0 그리고 터치 3봉 미만", [e for e in neg if e["touches"] < 3])
    log.info("==== 탐색 E2: 9/15 일지 고점돌파 재매수 + 6강 돌파매수 소량 (RS>0 사건)")
    report("E2 | BO 빼기 U (RS>0)", boot_paired(pos, lambda e: e["r_bo"] - e["r_u"], seed=21))
    report("E2 | WB 빼기 U (RS>0)", boot_paired(pos, lambda e: e["r_wb"] - e["r_u"], seed=22))
    report("E2 | WB 빼기 W (RS>0)", boot_paired(pos, lambda e: e["r_wb"] - e["r_w"], seed=23))
    for tag, s2 in [("RS>0", pos), ("RS<=0", neg)]:
        log.info(f"[DESC] E2 {tag} {len(s2)}건 | BO {fmt_pct(mean(e['r_bo'] for e in s2))} 돌파 발생 {mean(1.0 if e['bo_filled'] else 0.0 for e in s2):.0%} "
                 f"돌파 매수분만 {fmt_pct(mean(e['r_bo'] for e in s2 if e['bo_filled']))} | WB {fmt_pct(mean(e['r_wb'] for e in s2))} | "
                 f"0주로 끝나고 종가가 T가격 위: BO {mean(1.0 if (not e['bo_filled'] and e['move_after'] > 0) else 0.0 for e in s2):.0%} "
                 f"WB {mean(1.0 if (not e['bo_filled'] and not e['w_filled'] and e['move_after'] > 0) else 0.0 for e in s2):.0%} | "
                 f"산 것이 손실로 끝남: BO {mean(1.0 if (e['bo_filled'] and e['r_bo'] < 0) else 0.0 for e in s2):.0%} "
                 f"WB {mean(1.0 if ((e['bo_filled'] or e['w_filled']) and e['r_wb'] < 0) else 0.0 for e in s2):.0%}")

    # 판례 18: 사용자님 본인 체결
    log.info("==== 사용자님 9/8~9/28 하이닉스 정규장 매수 체결에 같은 구분 붙이기")
    user_fills_check(hy5, hy1, ks5, ks1)

    # 10/1 같은 정의 값
    log.info("==== 10/1 같은 정의로 잰 값")
    today_values()

    cols = ["code", "date", "t", "pc", "open", "p_t", "gap", "rs", "rs_pos", "x1", "kospi_above", "touches", "flow5",
            "close", "low_after", "high_after", "high_before", "revisit_open", "break_high", "legs", "legs_all",
            "move_after", "r_a", "r_w", "r_u", "r_h", "ladder", "bo_filled", "r_bo", "r_wb"]
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for e in ev:
            w.writerow([e[c] for c in cols])
    log.info(f"[OUT] {OUT_CSV} {len(ev)}행 저장")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"[FATAL] {type(e).__name__}: {e!r}\n{traceback.format_exc()}")
        sys.exit(1)
