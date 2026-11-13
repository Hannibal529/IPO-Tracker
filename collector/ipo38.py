"""38커뮤니케이션 공모주 페이지 수집.

공식 API가 없어 HTML 표를 읽는다. 페이지 구조가 바뀌면 이 파일만 고치면 되도록
'헤더 키워드로 표 찾기' 방식으로 작성. 요청 간 지연을 두고 하루 1~2회만 호출한다.
"""
import io
import re
import time

import pandas as pd
import requests

BASE = "http://www.38.co.kr/html/fund/index.htm"
UA = {"User-Agent": "Mozilla/5.0 (IPO tracker; team internal)"}


def _fetch(o, page=1):
    r = requests.get(BASE, params={"o": o, "page": page}, headers=UA, timeout=30)
    r.raise_for_status()
    r.encoding = "euc-kr"
    time.sleep(1.5)
    return r.text


def _table(html, must):
    for df in pd.read_html(io.StringIO(html)):
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
        df.columns = cols
        if all(any(m in c for c in cols) for m in must) and len(df) > 1:
            return df
    return None


def _name(s):
    s = re.sub(r"\(.*?\)", "", str(s))
    return s.replace("(유가)", "").strip()


def _num(s):
    s = re.sub(r"[^\d.]", "", str(s))
    return float(s) if s else None


def _dates(s, year):
    m = re.findall(r"(\d{4})?[./]?(\d{1,2})[./](\d{1,2})", str(s))
    out = []
    for y, mo, d in m[:2]:
        out.append(f"{int(y) if y else year:04d}-{int(mo):02d}-{int(d):02d}")
    return out or None


def schedule(pages=2):
    """공모주 청약일정: 종목명, 청약일, 확정공모가, 희망공모가, 경쟁률, 주간사"""
    rows = []
    for p in range(1, pages + 1):
        df = _table(_fetch("k", p), ["종목명", "주간사"])
        if df is None:
            break
        for _, r in df.iterrows():
            nm = _name(r.iloc[0])
            if not nm or nm == "nan":
                continue
            rec = {"name": nm}
            for c in df.columns:
                v = r[c]
                if "일정" in c:
                    d = _dates(v, time.localtime().tm_year)
                    if d:
                        rec["subscription"] = d if len(d) == 2 else [d[0], d[0]]
                elif "확정" in c:
                    rec["final_price"] = _num(v)
                elif "희망" in c:
                    nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]{3,}", str(v))]
                    if len(nums) >= 2:
                        rec["band"] = nums[:2]
                elif "경쟁" in c:
                    rec["retail_competition"] = str(v) if str(v) != "nan" else None
                elif "주간" in c:
                    rec["underwriter"] = str(v)
            rows.append(rec)
    return rows


def demand_results(pages=3):
    """수요예측결과: 예측일, 공모가, 기관경쟁률, 의무보유확약"""
    rows = []
    for p in range(1, pages + 1):
        df = _table(_fetch("r1", p), ["기업명", "경쟁률"])
        if df is None:
            break
        for _, r in df.iterrows():
            nm = _name(r.iloc[0])
            if not nm or nm == "nan":
                continue
            rec = {"name": nm}
            for c in df.columns:
                v = r[c]
                if "예측일" in c:
                    d = _dates(v, time.localtime().tm_year)
                    if d:
                        rec["forecast_date"] = d[0]
                elif "경쟁률" in c:
                    rec["inst_competition"] = _num(str(v).split(":")[0])
                elif "확약" in c:
                    rec["lock_ratio_38"] = _num(v) / 100 if _num(v) is not None else None
                elif c.strip() == "공모가" or ("공모가" in c and "희망" not in c):
                    rec["final_price"] = _num(v)
                elif "공모금액" in c:
                    rec["amount_eok"] = _num(v)
            rows.append(rec)
    return rows


def new_listings(pages=2):
    """신규상장: 상장일, 공모가, 시초가, 첫날 종가"""
    rows = []
    for p in range(1, pages + 1):
        df = _table(_fetch("nw", p), ["기업명", "상장일"])
        if df is None:
            break
        for _, r in df.iterrows():
            nm = _name(r.iloc[0])
            if not nm or nm == "nan":
                continue
            rec = {"name": nm}
            for c in df.columns:
                v = r[c]
                if "상장일" in c:
                    d = _dates(v, time.localtime().tm_year)
                    rec["listing"] = d[0] if d else None
                elif c.strip() == "시초가":
                    rec["open_price"] = _num(v)
                elif "종가" in c:
                    rec["first_close"] = _num(v)
                elif c.strip() == "공모가":
                    rec["final_price"] = _num(v)
            rows.append(rec)
    return rows
