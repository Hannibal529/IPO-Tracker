"""기존 수기 엑셀(IPO_YYYY-MM-DD.xlsx)을 사이트용 시드 JSON으로 변환.

사용: python -m collector.excel_seed <엑셀경로>
결과: data/seed.json  (수집기가 매번 이 위에 자동 수집 데이터를 덮어씀)
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent


def _d(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _range_md(text, year):
    """'9/2 ~ 9/8' -> ['2026-09-02','2026-09-08']"""
    if not text:
        return None
    m = re.findall(r"(\d{1,2})/(\d{1,2})", str(text))
    if not m:
        return None
    out = []
    for mo, dd in m[:2]:
        out.append(date(year, int(mo), int(dd)).isoformat())
    if len(out) == 1:
        out.append(out[0])
    # 연말→연초 넘어가는 경우 보정
    if out[1] < out[0]:
        out[1] = date(year + 1, *map(int, out[1][5:].split("-"))).isoformat()
    return out


def _band(text):
    if not text:
        return None
    nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", str(text)) if x.replace(",", "")]
    return nums[:2] if len(nums) >= 2 else None


def _multiple(v):
    """'PSR 11.15배' / 26.76 / 'EV/EBITDA 27.13' -> ('PER', 26.76)"""
    if v is None:
        return None, None
    if isinstance(v, (int, float)):
        return "PER", float(v)
    s = str(v).upper()
    method = "PER"
    for k in ("EV/EBITDA", "PSR", "PBR", "PER"):
        if k in s:
            method = k
            break
    n = re.findall(r"\d+(?:\.\d+)?", s.replace("EV/EBITDA", ""))
    return method, (float(n[0]) if n else None)


def convert(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["IPO스케쥴표"]
    rows = list(ws.iter_rows(values_only=True))

    # 1) 보호예수 해제 표 (J~X열, 상장일·종목명 헤더 아래)
    lockups = []
    for r in rows[4:]:
        if isinstance(r[9], datetime) and r[10]:
            periods = []
            for label, (di, qi, ri) in {
                "15일": (12, 13, 14), "1개월": (15, 16, 17),
                "3개월": (18, 19, 20), "6개월": (21, 22, 23),
            }.items():
                if r[qi]:
                    periods.append({"period": label, "date": _d(r[di]),
                                    "shares": r[qi], "ratio": r[ri]})
            lockups.append({"name": r[10], "listing": _d(r[9]),
                            "total_shares": r[11], "releases": periods})

    # 2) 공모 예정 표 (헤더 '회사명' 행 아래)
    hdr_idx = next(i for i, r in enumerate(rows) if r[1] == "회사명")
    pipeline = []
    for r in rows[hdr_idx + 1:]:
        name = r[1]
        if not name or str(name).startswith("*"):
            continue
        filed = r[5]
        year = (filed.year if isinstance(filed, datetime) else date.today().year)
        method, mult = _multiple(r[11])
        pipeline.append({
            "name": name, "market": r[2], "special": r[3] == "O",
            "underwriter": r[4], "first_filed": _d(r[5]), "last_amended": _d(r[6]),
            "forecast": _range_md(r[7], year), "subscription": _range_md(r[8], year),
            "payment": _d(r[9]), "listing": _d(r[10]),
            "ua_method": method, "ua_multiple": mult,
            "ua_value": _num(r[13]),
            "discount": r[19], "band": _band(r[20]),
            "final_price": _num(r[22]), "competition": r[23],
            "shares_offered": _num(r[24]), "exp_mcap": r[25], "amount": r[26],
            "business": r[28], "source": "excel",
        })

    # 3) 경쟁률 추이
    ws2 = wb["경쟁률추이"]
    demand = []
    for r in ws2.iter_rows(min_row=2, values_only=True):
        if not r[2]:
            continue
        amt = _num(r[4])
        if amt and amt > 1e6:  # 원 단위로 들어간 행 보정 → 억
            amt = round(amt / 1e8)
        demand.append({
            "quarter": r[0], "listing": _d(r[1]), "name": r[2],
            "inst_competition": _num(r[3]), "amount_eok": amt,
            "inst_count": _num(r[5]), "am_count": _num(r[6]),
            "total_qty": _num(r[7]), "inst_alloc": _num(r[8]),
            "am_qty": _num(r[9]), "am_lock_qty": _num(r[10]),
            "am_lock_ratio": _num(r[11]) if isinstance(r[11], (int, float)) else None,
            "am_part_ratio": _num(r[12]) if isinstance(r[12], (int, float)) else None,
            "series": {k: _num(r[i]) for k, i in
                       (("A", 18), ("B", 19), ("C", 20), ("Pre-IPO", 21)) if _num(r[i])},
            "source": "excel",
        })
    return {"pipeline": pipeline, "lockups": lockups, "demand": demand}


if __name__ == "__main__":
    src = sys.argv[1]
    out = ROOT / "data" / "seed.json"
    out.parent.mkdir(exist_ok=True)
    data = convert(src)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print({k: len(v) for k, v in data.items()}, "->", out)
