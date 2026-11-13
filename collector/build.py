"""수집 → 병합 → site/data/ipo.json 생성.

실행: python -m collector.build
환경변수: DART_API_KEY (필수), ANTHROPIC_API_KEY (선택: peer 검토를 Claude가 수행)
각 단계는 실패해도 다음 단계로 넘어가고, 실패 내용은 사이트 상단 경고로 표시된다.
"""
import json
import re
import traceback
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import holidays

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = DATA / "state.json"
SEED = DATA / "seed.json"
MANUAL = DATA / "manual.json"
OUT = ROOT / "site" / "data" / "ipo.json"

WARN = []
BIO_RE = r"바이오|신약|의약|제약|치료제|치료|항체|항암|임상|진단|헬스케어|정밀의료|테라퓨틱스|파마|단백질|파이프라인|기술이전|퇴행성|치매"
VALUATION_V = 6  # 재산정 로직 바꾸면 올림
PARSER_V = 6  # 파서를 고치면 올려서 기존 종목도 다시 읽게 함
KST = timezone(timedelta(hours=9))
REVALUE_BUDGET_SEC = 9 * 60  # 재산정에 쓸 최대 시간, 남은 종목은 다음 실행에서 계속
KR_HOL = holidays.country_holidays("KR", years=range(2024, 2031))


def norm(n):
    return re.sub(r"\s|\(.*?\)|㈜|주식회사", "", str(n or ""))


def is_bday(d):
    return d.weekday() < 5 and d not in KR_HOL and not (d.month == 12 and d.day == 31)


def workday_on_or_after(d):
    while not is_bday(d):
        d += timedelta(days=1)
    return d


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def release_date(listing, period):
    """엑셀 수식과 동일: 15일=WORKDAY(상장일+14,1), N개월=WORKDAY(EDATE-1,1)"""
    L = date.fromisoformat(listing)
    if period == "15일":
        return workday_on_or_after(L + timedelta(days=15)).isoformat()
    n = int(period.replace("개월", ""))
    return workday_on_or_after(add_months(L, n)).isoformat()


def step(name):
    def deco(fn):
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as e:
                WARN.append(f"[{name}] {type(e).__name__}: {e}")
                traceback.print_exc()
        return run
    return deco


def upsert(lst, rec, keep_existing=()):
    k = norm(rec["name"])
    for x in lst:
        if norm(x["name"]) == k:
            for f, v in rec.items():
                if v not in (None, "", [], {}) and f not in keep_existing:
                    x[f] = v
            return x
    lst.append(rec)
    return rec


# ---------------- DART ----------------
@step("DART 증권신고서")
def from_dart(state, dart, codes):
    from .filing_parser import Filing
    filings = dart.ipo_filings(days=120)
    wd = getattr(dart, "withdrawn", set())
    if wd:
        for nm, rn, dt in getattr(dart, "withdrawn_detail", []):
            print(f"  철회 제외: {nm} — {rn} ({dt})", flush=True)
        state["withdrawn"] = [{"name": nm, "report": rn, "date": _iso(dt)}
                              for nm, rn, dt in getattr(dart, "withdrawn_detail", [])]
        state["pipeline"] = [p for p in state["pipeline"] if p["name"] not in wd]
    by_corp = {}
    for f in filings:
        by_corp.setdefault(f["corp_code"], []).append(f)
    listed = {n for n, v in codes.items() if v["stock_code"]}
    today = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    todo = []
    for cc, fs in by_corp.items():
        fs.sort(key=lambda r: r["rcept_no"])
        name = fs[0]["corp_name"]
        ex = _find(state["pipeline"], name)
        # 이미 상장했거나, 상장 정보에 이미 올라가 있거나(상장 완료), 청약이 한 달 넘게 지난 종목은 건너뜀
        if name in listed or (ex and ex.get("listing") and ex["listing"] < today):
            continue
        if ex and ex.get("subscription") and ex["subscription"][-1] < cutoff:
            continue
        todo.append((cc, fs, name, ex))
    print(f"DART 증권신고서 {len(by_corp)}개사 중 진행 중 {len(todo)}개사 처리", flush=True)
    for cc, fs, name, ex in todo:
        print(f"  - {name}", flush=True)
        spac = "스팩" in name or "기업인수목적" in name
        orig = [f for f in fs if "정정" not in f["report_nm"]]
        amends = [f for f in fs if "정정" in f["report_nm"]]
        rec = {"name": name, "corp_code": cc, "spac": spac,
               "rcept_no": fs[-1]["rcept_no"],
               "first_filed": _iso(orig[0]["rcept_dt"]) if orig else _iso(fs[0]["rcept_dt"]),
               "last_amended": _iso(amends[-1]["rcept_dt"]) if amends else None}
        try:
            g = dart.equity_summary(cc, fs[0]["rcept_dt"], fs[-1]["rcept_dt"])
            gen = (g.get("일반사항") or [{}])[-1]
            sub = _range_kr(gen.get("sbd"))
            if sub:
                rec["subscription"] = sub
            pay = _range_kr(gen.get("pymd"))
            if pay:
                rec["payment"] = pay[0]
            kinds = g.get("증권의종류") or []
            if kinds:
                rec["shares_offered"] = _n(kinds[-1].get("stkcnt"))
            sale = sum(_n(v) or 0 for it in g.get("매출인에관한사항") or [] for k, v in it.items() if k == "slstk")
            rec["sale_shares"] = sale or 0
            uof = [(it.get("se"), _n(it.get("amt"))) for it in g.get("자금의사용목적") or [] if it.get("se")]
            if uof:
                rec["use_of_funds"] = [{"label": a, "amount": b} for a, b in uof if b]
            und = [u.get("actnmn") for u in g.get("인수인정보") or [] if "대표" in (u.get("actsen") or "")]
            if und:
                rec["underwriter"] = ", ".join(dict.fromkeys(und))
        except Exception as e:
            WARN.append(f"[estkRs {name}] {e}")
        if not spac and ex and ex.get("rcept_no") == fs[-1]["rcept_no"] and ex.get("parser_v") == PARSER_V:
            upsert(state["pipeline"], rec)  # 원문 변경 없음 → 일정만 갱신
            continue
        if not spac:
            # 최신 제출본부터 거슬러 올라가며 필요한 값을 채움 (정정신고서는 바뀐 부분만 있어서)
            summ, peers, fc, full, est, tni, fl, fin = {}, [], None, None, None, None, None, None
            for f in list(reversed(fs))[:4]:
                try:
                    d = Filing(dart.document(f["rcept_no"]))
                except Exception as e:
                    WARN.append(f"[원문 {name}] {e}")
                    continue
                sm = d.summary()
                est = est or d.estimate_info()
                tni = tni or d.target_ltm_ni()
                fl = fl or d.float_schedule()
                fin = fin or d.fin_summary()
                for k, v in sm.items():
                    summ.setdefault(k, v)
                fc = fc or d.forecast()
                if not peers:
                    peers = d.peer_table(summ.get("method"), summ.get("multiple")) or \
                        d.peer_table(summ.get("method"), sm.get("multiple"))
                if full is None and len(d.tables) > 500:
                    full = d
                if summ.get("band") and peers and fc and full is not None and tni and fl and fin:
                    break
            if not summ:
                WARN.append(f"[원문 {name}] 공모가 산정 요약표를 찾지 못함")
            rec.update({
                "band": summ.get("band"), "final_price": summ.get("final_price"),
                "ua_method": summ.get("method"), "ua_multiple": summ.get("multiple"),
                "ua_value": summ.get("value"), "discount": summ.get("discount"),
                "ua_base": summ.get("base"), "ua_shares": summ.get("shares"),
                "peers_ua": peers, "forecast": fc, "source": "dart", "parser_v": PARSER_V,
                "ua_est": est, "target_ltm": tni, "float_schedule": fl, "fin": fin,
            })
            # 파서가 다시 읽은 값은 비어 있어도 덮어씀 (이전 잘못된 값 제거)
            if ex:
                for k in ("peers_ua", "final_price", "ua_est", "our_valuation", "target_ltm"):
                    ex.pop(k, None)
            doc = full
            if doc is not None:
                head = doc.text[:60000]
                rec["market"] = "코스닥" if "코스닥시장" in head else ("유가증권" if "유가증권시장" in head else None)
                rec["special"] = bool(re.search("기술성장기업|기술특례|성장성\\s*특례", head))
                rec["business"] = doc.business_summary()
                rec["total_shares_after"] = doc.total_shares_after()
            rec["bio"] = bool(re.search(BIO_RE, name + " " + (rec.get("business") or "")))
        upsert(state["pipeline"], rec)


@step("DART 수요예측·확약")
def demand_from_dart(state, dart):
    """수요예측이 끝난 종목: 정정신고서/투자설명서/발행실적보고서에서 참여·확약 내역"""
    from .filing_parser import Filing
    today = date.today().isoformat()
    for ipo in state["pipeline"]:
        fc = ipo.get("forecast")
        if not ipo.get("corp_code") or not fc or fc[-1] > today or ipo.get("spac"):
            continue
        if ipo.get("demand_parsed") or fc[-1] < (date.today() - timedelta(days=60)).isoformat():
            continue
        reps = [r for r in dart.company_filings(ipo["corp_code"], days=120)
                if any(k in r["report_nm"] for k in ("투자설명서", "증권발행실적", "증권신고서"))]
        reps.sort(key=lambda r: r["rcept_no"], reverse=True)
        for r in reps[:4]:
            try:
                doc = Filing(dart.document(r["rcept_no"]))
            except Exception as e:
                WARN.append(f"[수요예측 원문 {ipo['name']}] {e}")
                continue
            dr = doc.demand_result()
            if dr.get("total_qty"):
                ipo["demand_stats"] = {k: v for k, v in dr.items() if k != "lock_by_period_applied"}
                ipo["lock_applied"] = dr.get("lock_by_period_applied")
                ipo["float_lock"] = doc.float_table()
                ipo["demand_parsed"] = r["rcept_no"]
                print(f"  수요예측 결과: {ipo['name']}", flush=True)
                break


# ---------------- 38 ----------------
@step("38커뮤니케이션")
def from_38(state):
    from . import ipo38
    for r in ipo38.schedule():
        x = _find(state["pipeline"], r["name"])
        if x:
            for k in ("subscription", "final_price", "retail_competition"):
                if r.get(k):
                    x[k] = r[k]
            if r.get("band") and not x.get("band"):
                x["band"] = r["band"]
    for r in ipo38.demand_results():
        x = _find(state["pipeline"], r["name"])
        if x:
            x["competition"] = f"{r['inst_competition']:,.2f} : 1" if r.get("inst_competition") else x.get("competition")
            x["lock_ratio_38"] = r.get("lock_ratio_38")
            if r.get("final_price"):
                x["final_price"] = r["final_price"]
        d = _find(state["demand"], r["name"])
        if d:
            d["inst_competition"] = d.get("inst_competition") or r.get("inst_competition")
            d["lock_ratio_38"] = r.get("lock_ratio_38")
    for r in ipo38.new_listings():
        x = _find(state["pipeline"], r["name"])
        if x:
            for k in ("listing", "open_price", "first_close"):
                if r.get(k):
                    x[k] = r[k]


# ---------------- 파생 데이터 ----------------
def roll_forward(state):
    """수요예측 끝난 종목 → 경쟁률 추이, 상장일 확정 종목 → 보호예수 일정"""
    for ipo in state["pipeline"]:
        if ipo.get("spac"):
            continue
        ds = ipo.get("demand_stats") or {}
        comp = _n(str(ipo.get("competition") or "").split(":")[0])
        if comp or ds:
            fp = ipo.get("final_price")
            amt = round(fp * ipo["shares_offered"] / 1e8) if fp and ipo.get("shares_offered") else None
            rec = {"name": ipo["name"], "listing": ipo.get("listing"), "inst_competition": comp,
                   "amount_eok": amt, **{k: ds.get(k) for k in
                   ("inst_count", "am_count", "total_qty", "am_qty", "am_lock_qty")},
                   "lock_ratio_38": ipo.get("lock_ratio_38") or ds.get("lock_ratio_applied")}
            if rec.get("am_qty") and rec.get("am_lock_qty"):
                rec["am_lock_ratio"] = rec["am_lock_qty"] / rec["am_qty"]
            if rec.get("am_qty") and rec.get("total_qty"):
                rec["am_part_ratio"] = rec["am_qty"] / rec["total_qty"]
            upsert(state["demand"], rec)
        if ipo.get("listing") and (ipo.get("lock_by_period") or ipo.get("float_lock")):
            total = ipo.get("total_shares_after")
            rel = []
            for p in ("15일", "1개월", "3개월", "6개월"):
                sh = ((ipo.get("lock_by_period") or {}).get(p) or 0) + ((ipo.get("float_lock") or {}).get(p) or 0)
                if sh:
                    rel.append({"period": p, "date": release_date(ipo["listing"], p), "shares": sh,
                                "ratio": sh / total if total else None})
            upsert(state["lockups"], {"name": ipo["name"], "listing": ipo["listing"],
                                      "total_shares": total, "releases": rel})
    # 엑셀 시드 중 해제일이 비어 있는 행도 계산
    for lk in state["lockups"]:
        for r in lk.get("releases", []):
            if not r.get("date") and lk.get("listing"):
                r["date"] = release_date(lk["listing"], r["period"])


@step("peer 재산정")
def revalue_all(state, dart, codes, manual):
    from .valuation import revalue, save_fin_cache
    start = time.time()
    # 일정이 가까운 종목부터
    order = sorted(state["pipeline"], key=lambda x: (x.get("forecast") or x.get("subscription") or ["9999"])[0])
    for ipo in order:
        if time.time() - start > REVALUE_BUDGET_SEC:
            WARN.append("[재산정] 시간 예산 초과 — 나머지 종목은 다음 실행에서 계산")
            break
        if ipo.get("spac") or not (2 <= len(ipo.get("peers_ua") or []) <= 15):
            continue  # peer 표를 제대로 못 읽은 종목은 재산정하지 않음
        if ipo.get("listing") and ipo["listing"] < date.today().isoformat():
            continue  # 상장 끝난 종목은 갱신 안 함
        m = dict((manual.get(ipo["name"]) or {}).get("valuation") or {})
        for k in ("bio_rate", "bio_sens", "max_multiple"):
            if (manual.get("_settings") or {}).get(k) and k not in m:
                m[k] = manual["_settings"][k]
        sig = json.dumps(m, sort_keys=True, ensure_ascii=False) + f"|{VALUATION_V}"
        ov = ipo.get("our_valuation") or {}
        if ov.get("as_of") == date.today().isoformat() and ov.get("sig") == sig:
            continue  # 오늘 같은 조건으로 이미 계산함
        print(f"  재산정: {ipo['name']}", flush=True)
        try:
            ipo["our_valuation"] = revalue(dart, codes, ipo, m)
            if ipo["our_valuation"]:
                ipo["our_valuation"]["sig"] = sig
        except Exception as e:
            WARN.append(f"[재산정 {ipo['name']}] {e}")
        save(state)
        save_fin_cache()
        print(f"    완료 ({time.time() - start:.0f}s 경과)", flush=True)


def apply_manual(state, manual):
    for name, ov in manual.items():
        if name.startswith("_"):
            continue
        x = _find(state["pipeline"], name)
        if x:
            for k, v in ov.items():
                if k != "valuation":
                    x[k] = v


# ---------------- 유틸 ----------------
def _find(lst, name):
    k = norm(name)
    return next((x for x in lst if norm(x["name"]) == k), None)


def _iso(s):
    s = re.sub(r"\D", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


def _range_kr(s):
    """'2026년 09월 10일 ~ 2026년 09월 11일' / '2026.09.10 ~ 09.11' 등"""
    if not s:
        return None
    ds = re.findall(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", s)
    out = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in ds[:2]]
    if len(out) == 1:
        out.append(out[0])
    return out or None


def _n(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v)))
    except ValueError:
        return None


def save(state):
    """단계마다 저장 → 중간에 시간 초과로 끊겨도 그때까지 수집한 데이터는 남음"""
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pub = {**state, "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "warnings": WARN}
    OUT.write_text(json.dumps(pub, ensure_ascii=False), encoding="utf-8")


def main():
    state = json.loads((STATE if STATE.exists() else SEED).read_text(encoding="utf-8"))
    # 공모 예정 목록은 DART 기준으로만 관리 (엑셀에서 온 값은 제거)
    state["pipeline"] = [p for p in state["pipeline"] if p.get("source") == "dart" or p.get("spac") is not None and p.get("corp_code") and p.get("source") != "excel"]
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else {}
    try:
        from .dart import Dart
        dart = Dart()
        codes = dart.corp_codes()
    except Exception as e:
        WARN.append(f"[DART 초기화] {e}")
        dart, codes = None, {}
    if dart:
        print("[1] DART 증권신고서", flush=True)
        from_dart(state, dart, codes)
        save(state)
    print("[2] 38커뮤니케이션", flush=True)
    from_38(state)
    if dart:
        print("[3] 수요예측 결과", flush=True)
        demand_from_dart(state, dart)
    apply_manual(state, manual)
    roll_forward(state)
    save(state)
    if dart:
        print("[4] peer 재산정", flush=True)
        revalue_all(state, dart, codes, manual)
    save(state)
    print(f"done: pipeline {len(state['pipeline'])}, demand {len(state['demand'])}, "
          f"lockups {len(state['lockups'])}, warnings {len(WARN)}")


if __name__ == "__main__":
    main()
