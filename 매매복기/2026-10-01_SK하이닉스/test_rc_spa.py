#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
여러 행동을 한꺼번에 비교했을 때 1등이 우연인지 보는 검정(White's Reality Check, Hansen's SPA).
backtesting-validation 스킬 1-2, 1-3 기준. 후보가 5개 이상이라 본페로니 대신 이 두 검정을 쓴다.

입력: lecture_test_events.csv (test_lecture_rules.py 출력), 지수보다 강했던(RS>0) 사건만
후보: A(T가격 즉시), H(반은 즉시, 반은 시가 대기), W(시가 대기), BO(당일 고가 돌파 매수), WB(반은 시가 대기, 반은 돌파), LAD(세 가격 사다리)
기준: U(전일종가 x 0.975 대기, 10/1 실제 대기 주문 깊이)와 0(아무것도 안 함) 두 가지
재표집: 날짜 순서를 유지하는 정상 부트스트랩(stationary bootstrap), 블록 길이 L = 2, 3, 5일, B = 5,000
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("rc_spa")

import csv
import math
import random
import traceback
from collections import OrderedDict

IN_CSV = "lecture_test_events.csv"
CANDS = ["r_a", "r_h", "r_w", "r_bo", "r_wb", "ladder"]
NAMES = {"r_a": "A", "r_h": "H", "r_w": "W", "r_bo": "BO", "r_wb": "WB", "ladder": "LAD", "r_u": "U"}
B = 5000
LS = [2, 3, 5]


def load():
    rows = list(csv.DictReader(open(IN_CSV, encoding="utf-8-sig")))
    ev = []
    for r in rows:
        if r["rs_pos"] != "True":
            continue
        e = {k: float(r[k]) for k in CANDS + ["r_u"]}
        e["date"] = r["date"]
        ev.append(e)
    ev.sort(key=lambda e: e["date"])
    log.info(f"[LOAD] {IN_CSV} | RS>0 사건 {len(ev)}건 | 날짜 {len(set(e['date'] for e in ev))}일")
    if len(ev) < 30:
        log.warning("[LOAD] 사건 30건 미만, 검정력 매우 낮음")
    return ev


def by_date(ev, bench):
    days = OrderedDict()
    for e in ev:
        days.setdefault(e["date"], []).append([e[k] - (e[bench] if bench else 0.0) for k in CANDS])
    return list(days.values())


def stationary_index(nd, L, rnd):
    idx, i = [], rnd.randrange(nd)
    while len(idx) < nd:
        idx.append(i)
        i = (i + 1) % nd if rnd.random() > 1.0 / L else rnd.randrange(nd)
    return idx


def means(day_blocks, pick):
    k = len(CANDS)
    s, n = [0.0] * k, 0
    for d in pick:
        for row in day_blocks[d]:
            for j in range(k):
                s[j] += row[j]
            n += 1
    return [x / n for x in s], n


def rc_spa(ev, bench, L, seed=5):
    blocks = by_date(ev, bench)
    nd = len(blocks)
    obs, n = means(blocks, range(nd))
    rnd = random.Random(seed + L)
    boots = []
    for _ in range(B):
        m, nb = means(blocks, stationary_index(nd, L, rnd))
        boots.append(m)
    k = len(CANDS)
    sq = math.sqrt(n)
    # 후보별 표준편차(√n 스케일)
    omega = []
    for j in range(k):
        xs = [sq * (b[j] - obs[j]) for b in boots]
        mu = sum(xs) / len(xs)
        omega.append(math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) or 1e-12)
    # RC (비학생화)
    v = max(sq * obs[j] for j in range(k))
    rc_p = sum(1 for b in boots if max(sq * (b[j] - obs[j]) for j in range(k)) >= v) / B
    # SPA (학생화, 일관 재중심화)
    t_obs = max(0.0, max(sq * obs[j] / omega[j] for j in range(k)))
    thr = -math.sqrt(2 * math.log(math.log(n)))
    g = [obs[j] if sq * obs[j] / omega[j] >= thr else 0.0 for j in range(k)]
    spa_p = sum(1 for b in boots if max(0.0, max(sq * (b[j] - g[j]) / omega[j] for j in range(k))) >= t_obs) / B
    best = max(range(k), key=lambda j: obs[j])
    return dict(n=n, nd=nd, obs=obs, best=CANDS[best], rc_p=rc_p, spa_p=spa_p)


def main():
    ev = load()
    for bench in ["r_u", None]:
        bname = "U(10/1 실제 깊이 대기)" if bench else "0(아무것도 안 함)"
        for L in LS:
            r = rc_spa(ev, bench, L)
            diffs = " ".join(f"{NAMES[c]} {r['obs'][j] * 100:+.2f}%p" for j, c in enumerate(CANDS))
            verdict = "채택" if (r["rc_p"] < 0.05 and r["spa_p"] < 0.05) else "보류(우연 배제 못함)"
            log.info(f"[RCSPA] 기준 {bname} | L={L}일 | 사건 {r['n']} 날짜 {r['nd']} | 기준 대비 평균 {diffs} | "
                     f"1등 {NAMES[r['best']]} | RC p={r['rc_p']:.3f} SPA p={r['spa_p']:.3f} | {verdict}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"[FATAL] {type(e).__name__}: {e!r}\n{traceback.format_exc()}")
        sys.exit(1)
