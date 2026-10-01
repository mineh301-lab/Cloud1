#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 모바일 증권 투자자별 일별 순매수(외국인, 기관, 개인) 수집.
8강 대전제("수급은 참고용 보조 지표, 앞으로의 방향성과 지속성이 중요") 검정용으로
사건일 직전 며칠의 외국인 순매수를 붙이기 위해 쓴다.
출력: data/flow/trend_<코드>.json (최신부터 과거 순서, 중복 제거)
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("fetch_flow")

import json
import os
import subprocess
import time

CODES = ["005930", "000660", "373220", "207940", "005380", "000270", "105560", "068270",
         "035420", "012450", "034020", "329180", "402340", "009150", "055550"]
OUT = "data/flow"
URL = "https://m.stock.naver.com/api/stock/{c}/trend?pageSize=60{extra}"


def get(url):
    r = subprocess.run(["curl", "-sS", "-m", "20", "-A", "Mozilla/5.0", url], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode} stderr={r.stderr!r}")
    if not r.stdout.strip():
        raise RuntimeError("빈 응답")
    return json.loads(r.stdout)


def main():
    os.makedirs(OUT, exist_ok=True)
    for c in CODES:
        rows = {}
        extra = ""
        for page in range(2):
            try:
                d = get(URL.format(c=c, extra=extra))
            except Exception as e:
                log.warning(f"[FETCH] {c} page{page} 실패 {type(e).__name__}: {e!r}")
                break
            for x in d:
                rows[x["bizdate"]] = x
            oldest = min(x["bizdate"] for x in d)
            # 다음 페이지는 가장 오래된 날의 전날부터 (bizdate 이하를 돌려줌)
            prev = time.strftime("%Y%m%d", time.localtime(time.mktime(time.strptime(oldest, "%Y%m%d")) - 86400))
            extra = f"&bizdate={prev}"
            log.info(f"[FETCH] {c} page{page} {len(d)}행 {oldest}~{max(x['bizdate'] for x in d)}")
            time.sleep(0.4)
        out = sorted(rows.values(), key=lambda x: x["bizdate"], reverse=True)
        json.dump(out, open(os.path.join(OUT, f"trend_{c}.json"), "w"), ensure_ascii=False)
        log.info(f"[OUT] {c} {len(out)}일 {out[-1]['bizdate']}~{out[0]['bizdate']}")
        if len(out) < 100:
            log.warning(f"[OUT] {c} 일수 {len(out)}, 120일 미만")


if __name__ == "__main__":
    main()
