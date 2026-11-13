"""Peer 그룹 재검토 + 최근 주가 기준 적정가 재산정.

흐름
1) 주관사 최종 유사기업(신고서 원문) + 같은 업종 상장사 후보를 모은다.
2) 각 기업의 현재 시가총액(FinanceDataReader)과 LTM 실적(DART 재무 API)으로
   PER / PBR / PSR / EV/EBITDA 를 직접 계산한다.
3) Peer 적합성 판단
   - ANTHROPIC_API_KEY 가 있으면 Claude 가 사업 유사성을 보고 제외/추가, 적용 지표를 결정
   - 없으면 규칙 기반: 적자·음수 멀티플 제외, 중앙값 대비 2.5배 초과/0.4배 미만 제외
4) 대상회사 기준이익은 '주관사 주당평가가액 ÷ 주관사 적용멀티플'(= 주당 기준이익)을 쓰고,
   여기에 재산정 멀티플(중앙값)을 곱해 주당 평가가액을 다시 계산한다.
   지표를 바꾸는 경우엔 data/manual.json 의 target_basis 에 해당 지표의 주당 기준값이 있어야 한다.
"""
import json
import os
import re
import statistics
from datetime import date
from pathlib import Path

import requests

try:
    import FinanceDataReader as fdr
except ImportError:  # 로컬 테스트용
    fdr = None

_LISTING = None


def listing():
    """KRX 상장사: Code, Name, Close, Marcap, Stocks, Industry"""
    global _LISTING
    if _LISTING is None:
        import signal

        def _to(*_):
            raise TimeoutError("상장사 시세 목록 조회 120초 초과")
        signal.signal(signal.SIGALRM, _to)
        signal.alarm(120)
        try:
            df = fdr.StockListing("KRX")
        finally:
            signal.alarm(0)
        try:
            desc = fdr.StockListing("KRX-DESC")[["Code", "Industry"]]
            df = df.merge(desc, on="Code", how="left")
        except Exception:
            df["Industry"] = None
        _LISTING = df.set_index("Name")
    return _LISTING


# ---------------- 재무 (LTM) ----------------
IDS = {
    "ni": ["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss"],
    "rev": ["ifrs-full_Revenue"],
    "op": ["dart_OperatingIncomeLoss"],
    "eq": ["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"],
    "cash": ["ifrs-full_CashAndCashEquivalents"],
}


def _amt(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pick(rows, key, field):
    for aid in IDS[key]:
        for r in rows:
            if r.get("account_id") == aid:
                v = _amt(r.get(field))
                if v is not None:
                    return v
    return None


def _da(rows, field):
    tot = 0
    for r in rows:
        if r.get("sj_div") == "CF" and any(k in r.get("account_nm", "")
                                           for k in ("감가상각비", "무형자산상각비", "사용권자산상각비")):
            tot += _amt(r.get(field)) or 0
    return tot or None


def _debt(rows):
    tot = 0
    for r in rows:
        if r.get("sj_div") == "BS" and any(k in r.get("account_nm", "") for k in ("차입금", "사채")) \
                and "상환" not in r.get("account_nm", ""):
            tot += _amt(r.get("thstrm_amount")) or 0
    return tot


FIN_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "fincache.json"
FIN_TTL_DAYS = 14  # 재무는 분기마다 바뀌므로 2주 캐시
_FIN = None


def _fin_cache():
    global _FIN
    if _FIN is None:
        try:
            _FIN = json.loads(FIN_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _FIN = {}
    return _FIN


def save_fin_cache():
    if _FIN is not None:
        FIN_CACHE_FILE.write_text(json.dumps(_FIN, ensure_ascii=False), encoding="utf-8")


def ltm(dart, corp_code):
    """캐시 확인 후 없으면 DART 조회 (peer 가 여러 공모주에 겹쳐도 한 번만)"""
    c = _fin_cache().get(corp_code)
    if c and (date.today() - date.fromisoformat(c["fetched"])).days < FIN_TTL_DAYS:
        return c["data"]
    data = _ltm_fetch(dart, corp_code)
    _fin_cache()[corp_code] = {"fetched": date.today().isoformat(), "data": data}
    return data


def _ltm_fetch(dart, corp_code):
    """최근 분기 누적 + 직전 연간 - 전년 동기 누적 = LTM (호출 최대 5회)"""
    y = date.today().year
    # 1) 직전 연간 보고서로 연결/별도 결정
    fy, fs_div, fy_year = None, None, None
    for yr in (y - 1, y - 2):
        for div in ("CFS", "OFS"):
            fy = dart.fs_all(corp_code, yr, "11011", div)
            if fy:
                fs_div, fy_year = div, yr
                break
        if fy:
            break
    if not fy:
        return None
    bs_src = fy
    # 2) 그 다음 해 최신 분기 누적
    q, rc = None, None
    if fy_year == y - 1:
        for rc in ("11014", "11012", "11013"):
            q = dart.fs_all(corp_code, y, rc, fs_div)
            if q:
                break
    if not q:
        flow = {k: _pick(fy, k, "thstrm_amount") for k in ("ni", "rev", "op")}
        flow["da"] = _da(fy, "thstrm_amount")
        basis = f"{fy_year} 연간"
    else:
        bs_src = q
        flow = {}
        for k in ("ni", "rev", "op"):
            a, cur, prev = _pick(fy, k, "thstrm_amount"), _pick(q, k, "thstrm_add_amount"), \
                _pick(q, k, "frmtrm_add_amount")
            if cur is None:  # 일부 회사는 누적 칸을 비워둠(반기 등)
                cur, prev = _pick(q, k, "thstrm_amount"), _pick(q, k, "frmtrm_amount")
            flow[k] = (a + cur - prev) if None not in (a, cur, prev) else a
        da_fy, da_cur, da_prev = _da(fy, "thstrm_amount"), _da(q, "thstrm_amount"), _da(q, "frmtrm_amount")
        flow["da"] = (da_fy + da_cur - da_prev) if None not in (da_fy, da_cur, da_prev) else da_fy
        basis = f"{y} {dict(zip(['11013', '11012', '11014'], ['1Q', '2Q', '3Q']))[rc]} LTM"
    bs = {"eq": _pick(bs_src, "eq", "thstrm_amount"), "cash": _pick(bs_src, "cash", "thstrm_amount"),
          "debt": _debt(bs_src)}
    return {**flow, **bs, "basis": basis, "fs_div": fs_div}


def multiples(mcap, f):
    if not f or not mcap:
        return {}
    out = {}
    if f.get("ni") and f["ni"] > 0:
        out["PER"] = mcap / f["ni"]
    if f.get("eq") and f["eq"] > 0:
        out["PBR"] = mcap / f["eq"]
    if f.get("rev") and f["rev"] > 0:
        out["PSR"] = mcap / f["rev"]
    ebitda = (f.get("op") or 0) + (f.get("da") or 0)
    if ebitda > 0:
        ev = mcap + (f.get("debt") or 0) - (f.get("cash") or 0)
        out["EV/EBITDA"] = ev / ebitda
    return {k: round(v, 2) for k, v in out.items()}


# 바이오 추정이익 현가 할인율 (data/manual.json 의 "_settings": {"bio_rates": [...]} 로 변경 가능)
BIO_RATES = (0.30, 0.35)
_AVG = {}


def avg_close(code, n=61):
    """최근 61영업일 종가 평균 (약 3개월) — IPO 보고서 기준"""
    if code in _AVG:
        return _AVG[code]
    try:
        from datetime import timedelta
        df = fdr.DataReader(code, (date.today() - timedelta(days=130)).isoformat())
        v = float(df["Close"].dropna().tail(n).mean()) if len(df) else None
    except Exception:
        v = None
    _AVG[code] = v
    return v


def pv_at(est, rate):
    """추정이익 현가를 다른 할인율로 재계산 (여러 연도면 평균)"""
    fv, t = est["fv"], est["t"]
    if len(fv) == len(t):
        return sum(a / (1 + rate) ** b for a, b in zip(fv, t)) / len(fv)
    return fv[0] / (1 + rate) ** t[0]


def _norm(n):
    return re.sub(r"\(주\)|㈜|주식회사|\s|[.,&-]", "", str(n)).upper()


_BY_CODE = None
_BY_NORM = None


def find_row(codes, name):
    """비교기업 찾기: ① DART 고유번호 목록의 종목코드 ② 거래소 종목명 ③ 공백·대소문자 무시 이름"""
    global _BY_CODE, _BY_NORM
    lst = listing()
    if _BY_CODE is None:
        _BY_CODE = {str(c): n for n, c in zip(lst.index, lst["Code"])}
        _BY_NORM = {_norm(n): n for n in lst.index}
    sc = (codes.get(name) or {}).get("stock_code")
    if not sc:
        k = _norm(name)
        sc = next((v["stock_code"] for n, v in codes.items() if v.get("stock_code") and _norm(n) == k), None)
    if sc and sc in _BY_CODE:
        return _BY_CODE[sc]
    if name in lst.index:
        return name
    k = _norm(name)
    if k in _BY_NORM:
        return _BY_NORM[k]
    for ko, en in _LETTER_PREFIX:  # 엘아이지디펜스… → LIG디펜스…
        if k.startswith(ko) and en + k[len(ko):] in _BY_NORM:
            return _BY_NORM[en + k[len(ko):]]
    for alt in _letter_variants(k):  # 알에프에이치아이씨 → RFHIC
        if alt in _BY_NORM:
            return _BY_NORM[alt]
    # 거래소 약칭이 신고서 표기의 앞부분인 경우 (인텔리안테크놀로지스 → 인텔리안테크)
    pre = [n for n in _BY_NORM if len(n) >= 4 and k.startswith(n)]
    if pre:
        return _BY_NORM[max(pre, key=len)]
    return None


_LETTERS = {"에이": "A", "비": "B", "씨": "C", "디": "D", "이": "E", "에프": "F", "지": "G", "에이치": "H",
            "아이": "I", "제이": "J", "케이": "K", "엘": "L", "엠": "M", "엔": "N", "오": "O", "피": "P",
            "큐": "Q", "알": "R", "에스": "S", "티": "T", "유": "U", "브이": "V", "더블유": "W", "엑스": "X",
            "와이": "Y", "제트": "Z"}


def _letter_variants(k, limit=20):
    """앞에서부터 알파벳 이름으로 읽을 수 있는 모든 경우를 영문으로 바꾼 후보"""
    out = []

    def go(i, acc):
        if len(out) >= limit:
            return
        if acc and len(acc) >= 2:
            out.append(acc + k[i:])
        for ko, en in _LETTERS.items():
            if k.startswith(ko, i):
                go(i + len(ko), acc + en)
    go(0, "")
    return sorted(set(out), key=len)


_LETTER_PREFIX = sorted([("엘아이지", "LIG"), ("에스케이", "SK"), ("엘지", "LG"), ("케이티앤지", "KT&G".replace("&", "")),
                         ("케이티", "KT"), ("씨제이", "CJ"), ("에이치디", "HD"), ("지에스", "GS"), ("에이치엘", "HL"),
                         ("디엘", "DL"), ("엘에스", "LS"), ("비지에프", "BGF"), ("케이씨씨", "KCC"), ("에스디", "SD"),
                         ("에이치케이", "HK"), ("에이치에스", "HS"), ("디비", "DB"), ("비엔케이", "BNK"), ("제이비", "JB"),
                         ("케이지", "KG"), ("에스씨", "SC"), ("티와이", "TY"), ("에이피", "AP"), ("오씨아이", "OCI"),
                         ("에이알", "AR"), ("알에프", "RF"), ("엔에이치", "NH"), ("아이에스", "IS"), ("에스엔", "SN")],
                        key=lambda x: -len(x[0]))


def peer_data(dart, codes, name):
    lst = listing()
    lname = find_row(codes, name)
    if not lname:
        return None
    row = lst.loc[lname]
    if getattr(row, "ndim", 1) > 1:  # 동명 종목이 여럿이면 시총 큰 쪽
        row = row.sort_values("Marcap", ascending=False).iloc[0]
    code = str(row.get("Code", ""))
    cc = (codes.get(name) or {}).get("corp_code") or (codes.get(lname) or {}).get("corp_code") \
        or next((v["corp_code"] for v in codes.values() if v.get("stock_code") == code), None)
    f = ltm(dart, cc) if cc else None
    shares = float(row["Marcap"]) / float(row["Close"]) if float(row["Close"]) else 0
    avg = avg_close(code)
    price = avg or float(row["Close"])
    mcap = price * shares
    return {"name": name if name == lname else f"{name} ({lname})", "code": code, "price": float(row["Close"]), "avg61": avg and round(avg),
            "mcap_eok": round(mcap / 1e8),
            "industry": row.get("Industry"), "fin_basis": f and f.get("basis"),
            "multiples": multiples(mcap, f)}


# ---------------- 적합성 판단 ----------------
MAX_MULTIPLE = 100  # 이보다 큰 배수는 이익이 0에 가까운 경우라 무조건 제외 (manual.json _settings.max_multiple)


def rule_filter(peers, method, cap=MAX_MULTIPLE):
    vals = [p["multiples"].get(method) for p in peers
            if p["multiples"].get(method) and p["multiples"][method] <= cap]
    med = statistics.median(vals) if vals else None
    for p in peers:
        m = p["multiples"].get(method)
        if m is None:
            p["kept"], p["reason"] = False, f"{method} 산출 불가(적자 또는 데이터 없음)"
        elif m > cap:
            p["kept"], p["reason"] = False, f"{m:.0f}배 — {cap}배 초과(이익 미미)로 제외"
        elif med and len(vals) >= 3 and (m > med * 2.5 or m < med * 0.4):
            p["kept"], p["reason"] = False, f"이상치 (중앙값 {med:.1f}배 대비 {m:.1f}배)"
        else:
            p["kept"], p["reason"] = True, "유지"
    return method, f"규칙 기반: 적자, {cap}배 초과, 이상치(3개사 이상일 때 중앙값의 2.5배 초과/0.4배 미만) 제외 후 평균 적용"


def claude_review(target, peers, candidates, method):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    prompt = f"""당신은 한국 IPO 밸류에이션 애널리스트입니다.
공모기업: {target['name']}
사업내용: {target.get('business') or '(미기재)'}
주관사 적용 지표: {method} / 주관사 적용 멀티플: {target.get('ua_multiple')}

주관사 유사기업(현재 주가·LTM 기준 재계산 멀티플):
{json.dumps([{k: p[k] for k in ('name','industry','mcap_eok','multiples')} for p in peers], ensure_ascii=False)}

추가 후보(같은 업종 상장사):
{json.dumps([{k: c[k] for k in ('name','industry','mcap_eok','multiples')} for c in candidates], ensure_ascii=False)}

할 일:
1. 공모기업과 사업 모델·매출 구조가 맞지 않거나, 멀티플이 비정상인 유사기업은 제외하고 이유를 쓰세요.
2. 추가 후보 중 공모기업과 실제로 비교 가능한 기업이 있으면 추가하세요(없으면 추가하지 마세요).
3. 적용 지표는 보통 주관사와 동일하게 두되, 명백히 부적절하면 바꿀 수 있습니다
   (바꾸면 대상회사 기준이익이 별도로 필요하므로 꼭 필요할 때만).
JSON 하나로만 답하세요:
{{"method":"PER","drop":[{{"name":"","reason":""}}],"add":[{{"name":"","reason":""}}],"rationale":"2~3문장"}}"""
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
              "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json()["content"])
    s, e = txt.find("{"), txt.rfind("}")
    return json.loads(txt[s:e + 1])


# ---------------- 메인 ----------------
def revalue(dart, codes, ipo, manual=None):
    manual = manual or {}
    method = manual.get("method") or ipo.get("ua_method") or "PER"
    ua_peers = ipo.get("peers_ua") or []
    peers, outside = [], []
    for q in ua_peers:
        d = peer_data(dart, codes, q["name"])
        if d:
            peers.append({**d, "ua_multiple": q.get("ua_multiple"), "origin": "주관사"})
        else:
            outside.append({"name": q["name"], "ua_multiple": q.get("ua_multiple"), "origin": "주관사",
                            "multiples": {}, "kept": False, "reason": "해외·비상장 등 국내 시세 없음 → 재산정 제외"})
    if not peers:
        return None

    # 같은 업종 후보 (시총 상위 12개)
    inds = {p["industry"] for p in peers if p.get("industry")}
    lst = listing()
    cand_names = []
    if inds:
        sub = lst[lst["Industry"].isin(inds)].sort_values("Marcap", ascending=False)
        cand_names = [n for n in sub.index[:20] if n not in {p["name"] for p in peers}][:12]

    review, candidates = None, []
    if os.environ.get("ANTHROPIC_API_KEY"):
        candidates = [d for d in (peer_data(dart, codes, n) for n in cand_names) if d]
        try:
            review = claude_review(ipo, peers, candidates, method)
        except Exception as e:  # API 실패 시 규칙 기반으로
            review = None
            ipo.setdefault("warnings", []).append(f"Claude 검토 실패: {e}")

    if review:
        new_method = review.get("method") or method
        basis_override = (manual.get("target_basis") or {}).get(new_method)
        if new_method != method and not basis_override:
            new_method = method  # 기준이익 없으면 지표 변경 불가
        method = new_method
        drops = {d["name"]: d["reason"] for d in review.get("drop", [])}
        for p in peers:
            p["kept"] = p["name"] not in drops and p["multiples"].get(method) is not None
            p["reason"] = drops.get(p["name"]) or ("유지" if p["kept"] else f"{method} 산출 불가")
        for a in review.get("add", []):
            c = next((c for c in candidates if c["name"] == a["name"]), None)
            if c and c["multiples"].get(method):
                peers.append({**c, "origin": "추가", "kept": True, "reason": a["reason"]})
        rationale = "Claude 검토: " + review.get("rationale", "")
    else:
        method, rationale = rule_filter(peers, method, manual.get("max_multiple") or MAX_MULTIPLE)

    # 수동 제외/추가 (manual.json) 최우선
    for n in manual.get("drop_peers", []):
        for p in peers:
            if p["name"] == n:
                p["kept"], p["reason"] = False, "수동 제외"
    for n in manual.get("add_peers", []):
        d = peer_data(dart, codes, n)
        if d:
            peers.append({**d, "origin": "수동 추가", "kept": d["multiples"].get(method) is not None,
                          "reason": "수동 추가"})

    peers += outside
    if not ipo.get("bio") and manual.get("bio") is None:
        inds = [str(p.get("industry") or "") for p in peers if p.get("industry")]
        if inds and sum(bool(re.search("의약|의료|바이오|생물|제약", i)) for i in inds) * 2 > len(inds):
            ipo["bio"] = True
    if manual.get("bio") is not None:
        ipo["bio"] = bool(manual["bio"])
    kept = [p["multiples"][method] for p in peers if p["kept"]]
    if not kept:
        return {"method": method, "peers": peers, "rationale": rationale, "value": None}
    avg = statistics.mean(kept)
    med = statistics.median(kept)
    m_ua, v_ua = ipo.get("ua_multiple"), ipo.get("ua_value")
    base, shares, est = ipo.get("ua_base"), ipo.get("ua_shares"), ipo.get("ua_est")
    same = method == ipo.get("ua_method")
    scen = {}  # 시나리오별 기준이익
    basis_note = None
    tb = (manual.get("target_basis") or {}).get(method)
    if tb is not None:
        scen["수동 입력"] = ("ps", tb)
    elif not same:
        return {"method": method, "peers": peers, "rationale": rationale, "value": None,
                "note": "주관사와 다른 지표 — manual.json 의 target_basis 필요"}
    elif method == "EV/EBITDA":
        if base and shares and v_ua and m_ua:
            scen["주관사 EBITDA"] = ("ev", None)
            basis_note = "EV/EBITDA: 순차입금·공모유입자금은 주관사 산정치 유지, 멀티플만 교체"
    elif est and ipo.get("bio") and shares:
        rates = manual.get("bio_rates") or BIO_RATES
        for r in rates:
            scen[f"할인율 {r * 100:.0f}%"] = ("ps", pv_at(est, r) / shares)
        basis_note = (f"바이오 추정이익: 주관사 할인율 {est['rate'] * 100:.0f}% 대신 "
                      + "·".join(f"{r * 100:.0f}%" for r in rates) + "로 현가 재계산")
    elif base and shares:
        scen["주관사 기준이익"] = ("ps", base / shares)
    elif v_ua and m_ua:
        scen["주관사 기준이익"] = ("ps", v_ua / m_ua)
    values = {}
    for k, (kind, ps) in scen.items():
        if kind == "ps":
            values[k] = round(avg * ps)
        else:
            values[k] = round(v_ua + base * (avg - m_ua) / shares)
    vs = sorted(values.values())
    out = {"method": method, "multiple_mean": round(avg, 2), "multiple_median": round(med, 2),
           "values": values, "value": round(sum(vs) / len(vs)) if vs else None,
           "value_range": [vs[0], vs[-1]] if len(vs) > 1 else None,
           "peers": peers, "rationale": rationale, "as_of": date.today().isoformat(),
           "price_basis": "비교기업 시가총액 = 최근 61영업일 종가 평균 × 상장주식수, 이익 = LTM 지배주주순이익",
           "note": basis_note}
    if out["value"] and ipo.get("band"):
        lo, hi = ipo["band"]
        out["band_discount"] = [round(1 - hi / out["value"], 4), round(1 - lo / out["value"], 4)]
    return out
