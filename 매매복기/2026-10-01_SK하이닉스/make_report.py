# -*- coding: utf-8 -*-
"""SK하이닉스 2026-10-01 오전 재매수 장면 1분봉 복기 보고서(HTML) 생성기.

입력: data/min_000660_20261001.json, data/min_KOSPI_20261001.json (네이버 1분봉, 9/30~10/1)
      data/day_orders.json (업로드 일별 주문내역 9/29~10/1을 parse_orders로 옮긴 것)
출력: 매매복기_20261001_SK하이닉스.html (lightweight-charts 4.2.0 CDN, 데이터는 HTML 안에 JSON으로 넣음)

시각은 한국 시각 벽시계를 UTC처럼 다뤄 차트 축에 그대로 보이게 한다.
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("make_report")

import calendar
import datetime as dt
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "매매복기_20261001_SK하이닉스.html")
DAY = "20261001"
PREV_DAY = "20260930"
CODE = "000660"
BAND = 0.0025                 # 전일종가 ±0.25%, test_wait_for_open.py 와 같은 값
CHECKPOINTS = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]


def to_ts(ldt):
    """'20261001093400' -> 한국 시각 벽시계를 UTC로 본 epoch 초."""
    d = dt.datetime.strptime(ldt, "%Y%m%d%H%M%S")
    return calendar.timegm(d.timetuple())


def load_min(name, day):
    path = os.path.join(DATA, name)
    log.info(f"[LOAD] {path} 읽기 시작")
    raw = json.load(open(path, encoding="utf-8"))
    rows = sorted([r for r in raw if r["localDateTime"].startswith(day)], key=lambda r: r["localDateTime"])
    if not rows:
        log.warning(f"[LOAD] {name} {day} 봉 0개")
    else:
        log.info(f"[LOAD] {name} {day} 봉 {len(rows)}개 {rows[0]['localDateTime'][8:12]}~{rows[-1]['localDateTime'][8:12]}")
    return rows


def regular_close(name, day):
    rows = load_min(name, day)
    reg = [r for r in rows if r["localDateTime"][8:12] <= "1530"]
    last = reg[-1]
    log.info(f"[LOAD] {name} {day} 정규장 마지막 봉 {last['localDateTime'][8:12]} 종가 {last['currentPrice']:,.2f}")
    return last["currentPrice"]


def bar_at(rows, hhmm):
    """hh:mm 에 시작한 1분봉의 행. 답변 본문의 '그 시각 화면 값'과 같은 기준이다.
    사전 등록 검정(test_wait_for_open.py)은 hh:mm 에 끝나는 5분봉 종가를 썼으므로 1분 차이가 난다."""
    key = DAY + hhmm.replace(":", "") + "00"
    for r in rows:
        if r["localDateTime"] == key:
            return r
    return None


def rs_pct(h, k, h_open, k_open):
    return ((h / h_open - 1) - (k / k_open - 1)) * 100


def main():
    hx = load_min("min_000660_20261001.json", DAY)
    kp = load_min("min_KOSPI_20261001.json", DAY)
    pc = regular_close("min_000660_20261001.json", PREV_DAY)
    kpc = regular_close("min_KOSPI_20261001.json", PREV_DAY)
    h_open = hx[0]["openPrice"]
    k_open = kp[0]["openPrice"]
    log.info(f"[CALC] 전일종가 {pc:,.0f} 당일시가 {h_open:,.0f} 갭 {h_open / pc - 1:+.2%} | 코스피 {kpc:,.2f} -> {k_open:,.2f} 갭 {k_open / kpc - 1:+.2%}")
    if pc != 1776000:
        log.warning(f"[CHECK] 전일종가가 답변 본문 값 1,776,000과 다름: {pc:,.0f}")

    kmap = {r["localDateTime"]: r for r in kp}
    bars, vols, rs = [], [], []
    missing_k = 0
    for r in hx:
        t = to_ts(r["localDateTime"])
        bars.append(dict(time=t, open=r["openPrice"], high=r["highPrice"], low=r["lowPrice"], close=r["currentPrice"]))
        up = r["currentPrice"] >= r["openPrice"]
        vols.append(dict(time=t, value=r["accumulatedTradingVolume"], color="rgba(239,68,68,0.35)" if up else "rgba(59,130,246,0.35)"))
        k = kmap.get(r["localDateTime"])
        if k is None:
            missing_k += 1
            continue
        rs.append(dict(time=t, value=round(rs_pct(r["currentPrice"], k["currentPrice"], h_open, k_open), 3)))
    if missing_k:
        log.warning(f"[CHECK] 코스피 봉이 없는 하이닉스 봉 {missing_k}개, 상대강도에서 뺌")
    last_ldt = hx[-1]["localDateTime"]
    last_ts = to_ts(last_ldt)
    log.info(f"[CALC] 상대강도 점 {len(rs)}개, 마지막 봉 {last_ldt[8:12]}")

    # 규칙 1 점검: 사전 등록 검정과 같은 30분 격자, 처음 전일종가 ±0.25% 안에 온 시각
    trig = None
    grid = []
    for cp in CHECKPOINTS:
        b, k = bar_at(hx, cp), bar_at(kp, cp)
        if b is None or k is None:
            grid.append(dict(t=cp, note="봉 없음"))
            continue
        p = b["currentPrice"]
        inband = abs(p / pc - 1) <= BAND
        r_now = rs_pct(p, k["currentPrice"], h_open, k_open)
        grid.append(dict(t=cp, price=p, vs_pc=(p / pc - 1) * 100, rs=r_now, inband=inband))
        if inband and trig is None:
            trig = cp
    for g in grid:
        log.info(f"[RULE1] {g}")
    rule1 = None
    if trig:
        t_prev = (dt.datetime.strptime(trig, "%H:%M") - dt.timedelta(minutes=30)).strftime("%H:%M")
        b0, k0 = bar_at(hx, t_prev), bar_at(kp, t_prev)
        r_prev = rs_pct(b0["currentPrice"], k0["currentPrice"], h_open, k_open)
        r_now = [g for g in grid if g["t"] == trig][0]["rs"]
        rule1 = dict(t=trig, t_prev=t_prev, rs_now=r_now, rs_prev=r_prev, ok=(r_now > 0 and r_prev > 0))
        log.info(f"[RULE1] 처음 밴드 진입 {trig} | 상대강도 지금 {r_now:+.2f}%p, 30분 전({t_prev}) {r_prev:+.2f}%p -> 통과 {rule1['ok']}")
    else:
        log.warning("[RULE1] 10:00~13:00 격자에서 전일종가 밴드 진입 없음")

    # 전일종가 터치: 1분 종가 교차 횟수와 위에서 내려와 건드린 봉 수(9:00~규칙 1 시각)
    cut = DAY + (trig or "13:00").replace(":", "") + "00"
    seg = [r for r in hx if r["localDateTime"] < cut]
    cross, touch_above, side = 0, 0, None
    first_cross = None
    for i, r in enumerate(seg):
        s = r["currentPrice"] > pc
        if r["currentPrice"] != pc:
            if side is not None and s != side:
                cross += 1
                first_cross = first_cross or r["localDateTime"][8:12]
            side = s
        if i > 0 and seg[i - 1]["currentPrice"] > pc and r["lowPrice"] <= pc:
            touch_above += 1
    log.info(f"[TOUCH] {cut[8:12]} 전까지 전일종가 1분 종가 교차 {cross}회(첫 교차 {first_cross}), 위에서 내려와 건드린 봉 {touch_above}개")
    seg11 = [r for r in hx if r["localDateTime"] < DAY + "110000"]
    cross11, side = 0, None
    touch11 = 0
    for i, r in enumerate(seg11):
        s = r["currentPrice"] > pc
        if r["currentPrice"] != pc:
            if side is not None and s != side:
                cross11 += 1
            side = s
        if i > 0 and seg11[i - 1]["currentPrice"] > pc and r["lowPrice"] <= pc:
            touch11 += 1
    run = 0
    best = (0, None, None)
    for r in seg11:
        if r["currentPrice"] < pc:
            run += 1
            if run > best[0]:
                best = (run, None, r["localDateTime"][8:12])
        else:
            run = 0
    log.info(f"[TOUCH] 11:00 전까지 교차 {cross11}회, 위에서 건드린 봉 {touch11}개, 전일종가 아래 연속 마감 최장 {best[0]}봉(끝 {best[2]})")

    after11 = [r for r in hx if r["localDateTime"] >= DAY + "110000"]
    low11 = min(after11, key=lambda r: r["lowPrice"])
    high11 = max(after11, key=lambda r: r["highPrice"])
    after1204 = [r for r in hx if r["localDateTime"] >= DAY + "120400"]
    low1204 = min(after1204, key=lambda r: r["lowPrice"])
    day_low = min(hx, key=lambda r: r["lowPrice"])
    log.info(f"[CALC] 11:00 이후 최저 {low11['lowPrice']:,.0f}({low11['localDateTime'][8:12]}) 최고 {high11['highPrice']:,.0f}({high11['localDateTime'][8:12]}) | 12:04 이후 최저 {low1204['lowPrice']:,.0f}({low1204['localDateTime'][8:12]}) | 당일 최저 {day_low['lowPrice']:,.0f}({day_low['localDateTime'][8:12]})")

    # 규칙 3 점검: 규칙 2 주문을 건 뒤 상대강도가 0 이하로 내려간 첫 봉
    rule3 = None
    if rule1 and rule1["ok"]:
        start = to_ts(DAY + rule1["t"].replace(":", "") + "00")
        for p in rs:
            if p["time"] >= start and p["value"] <= 0:
                rule3 = p["time"]
                break
        log.info(f"[RULE3] 규칙 2 주문 뒤 상대강도 0 이하 첫 봉 {dt.datetime.utcfromtimestamp(rule3).strftime('%H:%M') if rule3 else '없음'}")
    filled_rule2 = any(r["lowPrice"] < h_open for r in hx if rule1 and r["localDateTime"] >= DAY + rule1["t"].replace(":", "") + "00")
    log.info(f"[RULE2] 시가 {h_open:,.0f} 아래 거래(관통) 여부 {filled_rule2}")

    # 주문 이벤트 (하이닉스만)
    orders = json.load(open(os.path.join(DATA, "day_orders.json"), encoding="utf-8"))["2026-10-01"]
    orders = sorted([o for o in orders if o["종목번호"] == CODE], key=lambda o: (o["주문시간"], o["주문"]))
    log.info(f"[ORDER] 10/1 하이닉스 주문 행 {len(orders)}개")
    hmap = {r["localDateTime"][8:12]: r for r in hx}

    def minute_bar(hms):
        return hmap.get(hms[:2] + hms[3:5])

    events = []
    for o in orders:
        if o["주문시간"] < "09:00:00":
            continue
        b = minute_bar(o["주문시간"])
        low = b["lowPrice"] if b else None
        high = b["highPrice"] if b else None
        kind = o["구분"]
        if kind.endswith("취소") and o["거래소"] == "NXT":
            continue  # SOR 주문을 KRX로 다시 낼 때 함께 생긴 NXT 쪽 취소 행
        events.append(dict(time=o["주문시간"], kind=kind, price=o["주문가"], qty=o["주문량"], fill=o["체결가"],
                           status=o["상태"], venue=o["거래소"], low=low, high=high, no=o["주문"], orig=o["원주문"]))
    sells = [e for e in events if e["kind"].startswith("매도") and e["status"] == "체결"]
    fee = sum(o["수수료"] or 0 for o in orders if o["상태"] == "체결")
    tax = sum(o["세금"] or 0 for o in orders if o["상태"] == "체결")
    amt = sum((o["체결가"] or 0) * (o["체결량"] or 0) for o in orders if o["상태"] == "체결" and o["구분"].startswith("매도"))
    log.info(f"[COST] 매도 체결 {len(sells)}건 금액 {amt:,.0f} 수수료 {fee:,.0f} 세금 {tax:,.0f} 비용률 {(fee + tax) / amt:.4%}")
    for e in events:
        if e["kind"].startswith("매수") and e["price"] and e["low"]:
            log.info(f"[ORDER] {e['time']} {e['kind']} {e['price']:,.0f} 그 분 저가 {e['low']:,.0f} 차이 {e['price'] - e['low']:+,.0f}")

    def ts_of(hms):
        return to_ts(DAY + hms[:2] + hms[3:5] + "00")

    # SOR 주문을 한국거래소로 다시 낸 자리: 같은 초에 NXT 취소 행과 새 주문 행이 함께 있다
    reissue = {(o["주문시간"], o["구분"][:2]) for o in orders if o["구분"].endswith("취소") and o["거래소"] == "NXT"}
    markers = []
    for e in events:
        t = ts_of(e["time"])
        if e["kind"].startswith("매도") and e["status"] == "체결":
            markers.append(dict(time=t, position="aboveBar", role="sell", color="#f97316", shape="arrowDown", text=f"{e['fill']:,}"))
        elif e["kind"] == "매수" and e["status"] != "체결" and (e["time"], "매수") not in reissue:
            markers.append(dict(time=t, position="belowBar", role="buy", color="#22c55e", shape="circle", text=f"접수 {e['price']:,}"))
        elif e["kind"] == "매수정정":
            markers.append(dict(time=t, position="belowBar", role="mod", color="#94a3b8", shape="arrowUp", text=f"정정 {e['price']:,}"))
    # 같은 분에 같은 가격으로 두 번 체결된 매도(09:08)는 한 표시로 합친다
    merged = {}
    for m in sorted(markers, key=lambda m: m["time"]):
        key = (m["time"], m["position"], m["text"])
        if key in merged:
            merged[key]["n"] += 1
        else:
            merged[key] = dict(m, n=1)
    markers = []
    for m in sorted(merged.values(), key=lambda m: m["time"]):
        if m.pop("n") > 1:
            m["text"] = "2주 " + m["text"]
        markers.append(m)
    if rule1:
        markers.append(dict(time=ts_of(rule1["t"] + ":00"), position="aboveBar", role="rule", color="#2dd4bf", shape="square",
                            text=f"규칙 1 {'통과' if rule1['ok'] else '미통과'}"))
        markers = sorted(markers, key=lambda m: m["time"])
    log.info(f"[CHART] 표시 {len(markers)}개: " + ", ".join(f"{dt.datetime.utcfromtimestamp(m['time']).strftime('%H:%M')} {m['text']}" for m in markers))

    # 표: 매수 쪽과 매도 쪽 주문 행
    later = {}
    for o in orders:
        if o["원주문"] and o["원주문"] not in later:
            later[o["원주문"]] = o

    def result_of(o):
        if o["상태"] == "체결":
            return f"체결 {o['체결가']:,}", "ok"
        if o["구분"].endswith("취소"):
            return "취소 접수", ""
        nxt = later.get(o["주문"])
        if nxt is not None:
            word = "취소됨" if nxt["구분"].endswith("취소") else "정정됨"
            return f"{nxt['주문시간'][:5]} {word}", ""
        if o["미체결"]:
            return "미체결, 지금도 남아 있음", "miss"
        return "정정 접수", ""

    def row(o, side):
        b = minute_bar(o["주문시간"])
        rng = f"{b['lowPrice']:,.0f}~{b['highPrice']:,.0f}" if b else "장 시작 전"
        res, cls = result_of(o)
        price = f"{o['주문가']:,}" if o["주문가"] else ""
        if side == "buy":
            gap = ""
            if o["주문가"] and b and not o["구분"].endswith("취소"):
                d = o["주문가"] - b["lowPrice"]
                gap = f"<span class='miss'>저가보다 {abs(d):,.0f}원 아래</span>" if d < 0 else "<span class='ok'>저가 이상</span>"
            if (o["주문시간"], "매수") in reissue and o["구분"] == "매수":
                gap += " (SOR 주문을 한국거래소로 다시 냄)"
            last_col = gap
        else:
            last_col = o["거래소"]
        return (f"<tr><td class='t'>{o['주문시간']}</td><td class='k'>{o['구분']}</td><td class='num'>{price}</td><td class='num'>{o['주문량']}</td>"
                f"<td class='{cls}'>{res}</td><td class='num'>{rng}</td><td>{last_col}</td></tr>")

    buy_rows = [row(o, "buy") for o in orders if o["구분"].startswith("매수")]
    sell_rows = [row(o, "sell") for o in orders if o["구분"].startswith("매도")]
    log.info(f"[TABLE] 매수 쪽 {len(buy_rows)}행, 매도 쪽 {len(sell_rows)}행")

    # 주문 수명선: 실제 걸려 있던 매수 주문 가격과 기간
    life = [
        dict(price=1735000, start="09:36", end=last_ldt[8:10] + ":" + last_ldt[10:12], label="1,735,000 (09:36 접수, 지금도 미체결)"),
        dict(price=1732000, start="10:11", end="11:27", label="1,732,000 (10:11 접수)"),
        dict(price=1778000, start="11:27", end="11:31", label="1,778,000 (11:27 정정)"),
        dict(price=1781000, start="11:31", end="12:04", label="1,781,000 (11:31 정정)"),
        dict(price=1785000, start="12:04", end=last_ldt[8:10] + ":" + last_ldt[10:12], label="1,785,000 (12:04 정정, 지금도 미체결)"),
    ]
    for L in life:
        L["t0"] = ts_of(L["start"] + ":00")
        L["t1"] = ts_of(L["end"] + ":00")
    rule_line = None
    if rule1 and rule1["ok"]:
        end_t = rule3 or last_ts
        rule_line = dict(price=h_open, t0=ts_of(rule1["t"] + ":00"), t1=end_t,
                         label=f"규칙대로라면 시가 {int(h_open):,} 2주 ({rule1['t']}부터)")

    jumps = [
        dict(t="09:07", text="09:07 첫 매도 체결 1,760,000"),
        dict(t="09:36", text="09:36 1,735,000 매수 접수"),
        dict(t="10:08", text="10:08 마지막 1주 매도 1,788,000"),
        dict(t="10:11", text="10:11 1,732,000 매수 접수"),
    ]
    if rule1:
        jumps.append(dict(t=rule1["t"], text=f"{rule1['t']} 규칙 1 점검"))
    jumps += [
        dict(t="11:00", text="11:00 고민하신 시각"),
        dict(t="11:27", text="11:27 첫 상향 정정"),
        dict(t="12:04", text="12:04 1,785,000으로 정정"),
    ]
    jumps = sorted(jumps, key=lambda j: j["t"])
    for j in jumps:
        j["ts"] = ts_of(j["t"] + ":00")

    payload = dict(bars=bars, vols=vols, rs=rs, markers=markers, life=life, rule_line=rule_line,
                   pc=pc, open=h_open, jumps=jumps, last=last_ldt[8:10] + ":" + last_ldt[10:12])
    summary = dict(pc=pc, h_open=h_open, gap=h_open / pc - 1, k_gap=k_open / kpc - 1, grid=grid, rule1=rule1,
                   cross11=cross11, touch11=touch11, under_run=best, cross_trig=cross, touch_trig=touch_above,
                   low11=(low11["lowPrice"], low11["localDateTime"][8:12]), high11=(high11["highPrice"], high11["localDateTime"][8:12]),
                   low1204=(low1204["lowPrice"], low1204["localDateTime"][8:12]), day_low=(day_low["lowPrice"], day_low["localDateTime"][8:12]),
                   rule3=dt.datetime.utcfromtimestamp(rule3).strftime("%H:%M") if rule3 else None, filled_rule2=filled_rule2,
                   sell_amt=amt, fee=fee, tax=tax, last=payload["last"])
    json.dump(summary, open(os.path.join(HERE, "report_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    log.info(f"[OUT] 요약 저장 report_summary.json")

    tpl = open(os.path.join(HERE, "report_template.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    r1 = rule1 or {}
    neg = [dt.datetime.utcfromtimestamp(p["time"]).strftime("%H:%M") for p in rs if p["value"] <= 0]
    rs_neg_text = ("과 ".join(neg) + f" {len(neg)}분") if 0 < len(neg) <= 3 else (f"{len(neg)}분" if neg else "단 한 분도 없이")
    log.info(f"[CALC] 지수 대비 강도 0 이하 분 {len(neg)}개 {neg[:10]}")
    r1row = [g for g in grid if g.get("t") == r1.get("t")]
    low_r1 = min(r["lowPrice"] for r in hx if rule1 and r["localDateTime"] >= DAY + rule1["t"].replace(":", "") + "00")
    log.info(f"[CALC] 규칙 1 시각 이후 최저 {low_r1:,.0f}")
    fills = dict(LAST=payload["last"], FEE=f"{fee:,.0f}", TAX=f"{tax:,.0f}", AMT=f"{amt:,.0f}",
                 COSTPCT=f"{(fee + tax) / amt:.2%}", COST=f"{fee + tax:,.0f}",
                 R1T=r1.get("t", "없음"), R1PRICE=f"{r1row[0]['price']:,.0f}" if r1row else "",
                 R1VS=f"{r1row[0]['vs_pc']:+.2f}%" if r1row else "", R1RS=f"{r1.get('rs_now', 0):+.2f}%p",
                 R1PREVT=r1.get("t_prev", ""), R1PREV=f"{r1.get('rs_prev', 0):+.2f}%p",
                 CROSS=str(cross), TOUCH=str(touch_above), LOWR1=f"{low_r1:,.0f}",
                 RULE3=(dt.datetime.utcfromtimestamp(rule3).strftime("%H:%M") + "였습니다") if rule3 else f"{payload['last']}까지 없었습니다",
                 RSNEG=rs_neg_text, BUY_ROWS="\n".join(buy_rows), SELL_ROWS="\n".join(sell_rows), SELL_COUNT=str(len(sell_rows)))
    for k, v in fills.items():
        html = html.replace("{{" + k + "}}", v)
    if "{{" in html:
        log.warning("[OUT] 채워지지 않은 자리표시가 남음")
    open(OUT, "w", encoding="utf-8").write(html)
    log.info(f"[OUT] {OUT} {len(html):,}자")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"[FATAL] {type(e).__name__}: {e!r}\n{traceback.format_exc()}")
        sys.exit(1)
