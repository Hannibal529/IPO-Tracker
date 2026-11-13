"""증권신고서/투자설명서/증권발행실적보고서 원문(XML)에서 필요한 값 추출.

DART 원문은 회사·주관사마다 표 양식이 조금씩 달라서 '키워드로 표/행 찾기' 방식의
휴리스틱으로 뽑는다. 못 찾은 값은 None 으로 두고, data/manual.json 으로 보정한다.
"""
import re

import lxml.html

NUM = r"-?\d[\d,]*(?:\.\d+)?"
METHODS = ("EV/EBITDA", "PER", "PSR", "PBR")


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _to_num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("배", "").replace("원", "").replace("주", "").replace("x", "").replace("X", "").replace("%", "").strip()
    s = s.replace("(", "-").replace(")", "") if re.fullmatch(r"\([\d.]+\)", s) else s
    try:
        return float(s)
    except ValueError:
        m = re.search(NUM, s)
        return float(m.group().replace(",", "")) if m else None


def _is_num(c):
    return bool(re.fullmatch(r"\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*(배|x|X|원|%)?\s*", c or ""))


_SKIP = re.compile(r"^(구\s*분|산\s*식|비\s*고|단위.*|평균|합계|항목|기업명|회사명|국내|해외|통화|"
                   r"[A-Z]{3}|-|[A-Z]|[①-⑳]|\[.*\]|\(.*\)|.*[=÷×].*)$")


def _name_like(c):
    c = (c or "").strip()
    return bool(c) and not _SKIP.match(c) and not re.search(r"\d", c) and len(c) <= 40


def _clean_name(n):
    return re.sub(r"\(주\)|㈜|주식회사|\s", "", n)


class Filing:
    def __init__(self, xml):
        # BeautifulSoup 대신 lxml 로 한 번만 순회 (수백 페이지 문서도 수 초 내)
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8", "ignore")
        xml = re.sub(r"<\?xml[^>]*\?>", "", xml)  # 유니코드 문자열엔 인코딩 선언 불가
        root = lxml.html.fromstring(xml)
        self.text = _clean(root.text_content())
        self.tables = []
        last_title = ""
        for el in root.iter():
            tag = el.tag.lower() if isinstance(el.tag, str) else ""
            if tag in ("p", "title", "span") and el.getparent() is not None \
                    and not any(a.tag.lower() == "table" for a in el.iterancestors() if isinstance(a.tag, str)):
                t = _clean(el.text_content())
                if t:
                    last_title = t[:200]
            elif tag == "table":
                rows = []
                for tr in el.iter("tr", "TR"):
                    cells = [_clean(c.text_content()) for c in tr
                             if isinstance(c.tag, str) and c.tag.lower() in ("td", "th", "te", "tu")]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    self.tables.append({"rows": rows, "title": last_title})


    # ================= 표준 양식 기반 추출 (2026-11 실제 신고서 16건으로 검증) =================
    def summary(self):
        """[공모가 산정 요약표] — 모든 신고서에 들어가는 표준 표. 정정신고서는 마지막 표가 정정 후."""
        tabs = [t["rows"] for t in self.tables
                if t["rows"][0][:1] == ["평가방법"] or (len(t["rows"]) > 1 and t["rows"][1][:1] == ["평가모형"])]
        if not tabs:
            return {}
        out = {}
        for r in tabs[-1]:
            lab = r[0]
            head = " ".join(r[:2])
            joined = " ".join(r)
            if lab == "평가모형":
                rest = " ".join(r[1:]).upper()
                out["method"] = next((m for m in METHODS if m in rest), rest[:20])
                continue
            if lab in ("평가방법", "적용 재무수치", "적용재무수치", "적용산식", "적용근거"):
                continue
            if lab == "①" and "base" not in out:  # 적용 이익/EBITDA (원 단위로 환산)
                cells = r[1:]
                unit = 1e6 if any("백만원" in c for c in cells) else (1e3 if any("천원" in c for c in cells) else 1)
                vals = [_to_num(c) for c in cells[1:] if re.search(r"\d", c) and _to_num(c)]
                if vals:
                    out["base"] = vals[0] * unit
            if re.search("주식수", head) and "shares" not in out and lab in "①②③④⑤⑥⑦":
                vals = [_to_num(c) for c in r[1:] if re.search(r"\d{1,3}(,\d{3})+", c)]
                if vals:
                    out["shares"] = vals[0]
            m = out.get("method")
            if m and "multiple" not in out and m in head.upper() and re.search("비교|유사|평균|적용", head):
                vals = [c for c in r[1:] if _is_num(c)]
                if vals:
                    out["multiple"] = _to_num(vals[0])
            if re.search(r"주당\s*평가\s*가", lab) and "할인" not in lab and "value" not in out:
                vals = [_to_num(c) for c in r[1:] if _is_num(c)]
                vals = [v for v in vals if v and v >= 100]
                if vals:
                    out["value"] = vals[0]
            if ("평가가액에 대한" in head or "할인율" in lab) and "discount" not in out:
                pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%?", " ".join(c for c in r[1:] if re.search(r"%|\d+\.\d+\s*~", c)))
                if pcts:
                    p = sorted(float(x) for x in pcts[:2])
                    out["discount"] = " ~ ".join(f"{x:.2f}%" for x in p)
            if re.search(r"공모\s*가\s*산정|공모가액\s*밴드|확정\s*공모\s*가", lab):
                nums = list(dict.fromkeys(int(x.replace(",", "")) for x in
                                          re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,6}", " ".join(r[1:3]))))
                done = "확정" in lab or ("밴드" not in lab and len(nums) == 1
                                          and re.search(r"결정\s*하였|으로\s*결정|확정\s*하였", joined))
                if done and nums:
                    out["final_price"] = nums[-1]
                    continue
                if len(nums) >= 2:
                    out["band"] = nums[:2]
        return out

    def peer_table(self, method, mult):
        """최종 멀티플이 계산된 표에서 비교기업과 개별 배수 추출 (회사가 열/행 어느 방향이든)"""
        if not method:
            return []
        method = method.upper()
        cand = []
        for t in self.tables:
            rows = t["rows"]
            if rows[0][:1] == ["평가방법"]:
                continue
            nums = [_to_num(c) for r in rows for c in r if _is_num(c)]
            if mult and not any(v is not None and abs(v - mult) < 0.006 for v in nums):
                continue
            # (1) 회사가 열 방향: 숫자 없는 행 중 회사명이 가장 많은 행을 헤더로
            first_num = next((k for k, r in enumerate(rows) if any(_is_num(c) for c in r[1:])), len(rows))
            best = []
            for r in rows[:first_num]:
                nm = [_clean_name(c) for c in r if _name_like(c)]
                if len(nm) > len(best):
                    best = nm
            mrow = None
            for r in rows:
                lab = " ".join(r[:2]).upper()
                if method in lab and not re.search("평균|적용|거래배수|최종|전체|국가별", r[0]) \
                        and sum(_is_num(c) for c in r) >= len(best) >= 2:
                    mrow = r
            if len(best) >= 2 and mrow:
                vals = [_to_num(c) for c in mrow if _is_num(c)][-len(best):]
                cand.append([{"name": n, "ua_multiple": v} for n, v in zip(best, vals)])
                continue
            # (2) 회사가 행 방향: 헤더에 지표 열이 있음
            hdr = rows[0]
            ci = next((j for j, c in enumerate(hdr) if method in c.upper()), None)
            if ci is not None:
                ps = []
                for r in rows[1:]:
                    if r and _name_like(r[0]) and len(r) == len(hdr) and _is_num(r[ci]):
                        ps.append({"name": _clean_name(r[0]), "ua_multiple": _to_num(r[ci])})
                if len(ps) >= 2:
                    cand.append(ps)
        return cand[-1] if cand else []

    def estimate_info(self):
        """추정이익 현가 계산표: 미래 추정이익(원), 주관사 연할인율, 할인기간(지수) → 할인율 바꿔 재계산용"""
        best = None
        for t in self.tables:
            rows = t["rows"]
            if rows[0][:1] == ["평가방법"]:
                continue
            rate_rows = [r for r in rows if r and re.search(r"할인율", r[0]) and "평가가액" not in r[0]]
            pv_rows = [r for r in rows if any("^" in c for c in r)]
            if not rate_rows or not pv_rows:
                continue
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", " ".join(rate_rows[0][1:]))
            if not m:
                continue
            fvs, yrs = [], []
            for r in rows:
                if r and re.search(r"20\d\d.*추정", r[0]) and "현재가치" not in r[0] and "현가" not in r[0]:
                    yrs.append(re.search(r"20\d\d", r[0]).group())
                    cells = " ".join(r[1:2])
                    unit = 1e6 if "백만원" in cells else (1e3 if "천원" in cells else 1)
                    v = _to_num(r[1]) if len(r) > 1 else None
                    if v:
                        fvs.append(v * unit)
            exps = [float(x) for x in re.findall(r"\^\s*\(?\s*(\d+(?:\.\d+)?)", " ".join(" ".join(r) for r in pv_rows))]
            if fvs and exps:
                best = {"rate": float(m.group(1)) / 100, "fv": fvs, "t": exps, "years": yrs[:len(fvs)]}
        return best

    def demand_result(self):
        """의무보유확약기간별 수요예측 참여내역 (국내 표 + 외국/합계 표) → 엑셀 '경쟁률추이' 항목"""
        PERIODS = ("15일", "1개월", "3개월", "6개월")
        groups_data = {}  # 그룹명 → {행라벨: (건수, 수량)}
        for t in self.tables:
            rows = t["rows"]
            if not any(r and r[0].replace(" ", "") == "미확약" for r in rows):
                continue
            if not any("확약" in r[0] for r in rows if r):
                continue
            # 그룹 행: 운용사/합계/외국 등이 들어 있는 행, 그 다음 '건수, 수량, 신청가격' 반복 행
            ti = next((k for k, r in enumerate(rows) if r and r[0] == "건수"), None)
            if ti is None or ti == 0:
                continue
            n = max(1, rows[ti].count("건수"))
            per = max(2, len(rows[ti]) // n)

            def _g(r):
                return [c for c in r if c and not re.fullmatch(r"구\s*분|(국내|해외|외국)\s*기관투자자", c)]
            grow = _g(rows[ti - 1])
            if grow and all(c.replace(" ", "") in ("공모", "사모") for c in grow) and ti >= 2:
                parent = _g(rows[ti - 2])  # '운용사(집합)' 아래에 '공모/사모'가 따로 있는 양식
                grow = [parent[0] + c for c in grow] + parent[1:]
            if len(grow) < n and ti >= 2:  # 3단 헤더: 상위 행의 '합 계' 등을 뒤에 붙임
                grow += _g(rows[ti - 2])[-(n - len(grow)):]
            for r in rows[ti + 1:]:
                if not r:
                    continue
                lab = r[0].replace(" ", "")
                vals = r[1:]
                for gi, g in enumerate(grow):
                    seg = vals[gi * per: gi * per + per]
                    if len(seg) < 2:
                        continue
                    cnt = _to_num(seg[0]) if seg[0] not in ("-", "") else 0
                    qty = _to_num(seg[1]) if seg[1] not in ("-", "") else 0
                    groups_data.setdefault(g.replace(" ", ""), {})[lab] = (cnt or 0, qty or 0)
        if not groups_data:
            return {}
        am = [g for g in groups_data if g.startswith("운용사(집합)")]
        tot = groups_data.get("합계")
        out = {}
        if tot and "합계" in tot:
            out["inst_count"], out["total_qty"] = tot["합계"]
            out["lock_by_period_applied"] = {p: tot.get(f"{p}확약", (0, 0))[1] for p in PERIODS}
            lk = sum(out["lock_by_period_applied"].values())
            out["lock_ratio_applied"] = lk / out["total_qty"] if out["total_qty"] else None
        if am:
            out["am_count"] = sum(groups_data[g].get("합계", (0, 0))[0] for g in am)
            out["am_qty"] = sum(groups_data[g].get("합계", (0, 0))[1] for g in am)
            out["am_lock_qty"] = sum(groups_data[g].get(f"{p}확약", (0, 0))[1] for g in am for p in PERIODS)
        return out

    def target_ltm_ni(self):
        """공모기업 자체 LTM 순이익(지배) — 재무제표 요약표의 '당기 누적 + 전년 연간 − 전년 동기 누적'"""
        def colinfo(c):
            c = c.replace(" ", "")
            m = re.search(r"(20\d\d)년", c)
            if not m:
                return None
            y = int(m.group(1))
            if re.search(r"3분기|9월", c):
                return y, "3Q"
            if "반기" in c or "6월" in c:
                return y, "H"
            if re.search(r"1분기|3월", c):
                return y, "1Q"
            if "분기" in c:
                return None
            return y, "FY"
        for i, t in enumerate(self.tables):
            rows = t["rows"]
            if len(rows) < 3 or len(rows[0]) < 4:
                continue
            info = [colinfo(c) for c in rows[0]]
            inter = [(k, v) for k, v in enumerate(info) if v and v[1] != "FY"]
            if len(inter) < 2:
                continue
            cur_k, (cy, ct) = max(inter, key=lambda kv: kv[1][0])
            prev_k = next((k for k, v in inter if v == (cy - 1, ct)), None)
            fy_k = next((k for k, v in enumerate(info) if v == (cy - 1, "FY")), None)
            if prev_k is None or fy_k is None:
                continue
            lab_rows = [r for r in rows if r and re.search(r"(당기|반기|분기)순이익|순손익", r[0].replace(" ", ""))]
            ctrl = [r for r in rows if r and "지배기업" in r[0] and "비지배" not in r[0]]
            pick = (ctrl or lab_rows)
            if not pick:
                continue
            r = pick[0]
            off = len(rows[0]) - len(r)  # 라벨 열 병합 차이 보정
            try:
                vals = [_to_num(r[k - off]) for k in (cur_k, fy_k, prev_k)]
            except IndexError:
                continue
            if None in vals:
                continue
            unit = 1
            if max(abs(v) for v in vals) < 1e8:  # 원 단위가 아니면 앞 표의 단위 표기 확인
                near = " ".join(" ".join(" ".join(x) for x in self.tables[j]["rows"][:2]) for j in range(max(0, i - 2), i + 1))
                unit = 1e6 if "백만원" in near else (1e3 if "천원" in near else 1)
            ltm = (vals[0] + vals[1] - vals[2]) * unit
            return {"ni": ltm, "basis": f"{cy}년 {dict(H='반기', **{'3Q': '3분기', '1Q': '1분기'})[ct]} LTM"}
        return None

    def float_schedule(self):
        """상장 후 기간별 누적 유통가능주식수·비율 (공모 후 기준)"""
        lab_re = re.compile(r"상장\s*(일|직후)|상장\s*후\s*(\d+)\s*(개월|년)")
        for t in self.tables:
            rows = [r for r in t["rows"] if r and lab_re.search(r[0])]
            if len(rows) < 3 or "유통" not in " ".join(" ".join(r) for r in t["rows"][:3]):
                continue
            width = min(len(r) for r in rows) - 1
            pick = None
            for k in range(width):
                vals = [_to_num(r[k + 1]) if r[k + 1] not in ("-", "") else None for r in rows]
                nums = [x for x in vals if x is not None]
                if len(nums) >= len(rows) - 1 and all(x >= 1000 for x in nums) \
                        and all(b >= a for a, b in zip(nums, nums[1:])):
                    pick = k
                    break
            if pick is None:
                continue
            out = []
            for r in rows:
                m = lab_re.search(r[0])
                lab = "상장일" if m.group(1) else f"{m.group(2)}{m.group(3)}"
                sh = _to_num(r[pick + 1]) if r[pick + 1] not in ("-", "") else (out[-1]["shares"] if out else None)
                ratio = None
                if pick + 2 < len(r) and r[pick + 2] not in ("-", ""):
                    v = _to_num(r[pick + 2])
                    ratio = v / 100 if v is not None and v > 1 else v
                out.append({"label": lab, "shares": sh, "ratio": ratio})
            return out
        return []

    def fin_summary(self):
        """요약 재무: 최근 분기·반기 + 직전 2개 연도 × 주요 계정 (억원). BS·IS 각각 가장 충실한 표에서 추출"""
        from datetime import date as _d
        cy = _d.today().year
        bs_keys = {"자산총계": r"^자산총계", "부채총계": r"^부채총계", "자본금": r"^자본금", "자본총계": r"^자본총계"}
        is_keys = {"매출액": r"^(매출액|영업수익)", "영업이익": r"^영업이익",
                   "당기순이익": r"^(당기|반기|분기)순이익"}

        def periods(hdr):
            ns = [int(x) for c in hdr for x in re.findall(r"제\s*(\d+)\s*기", c)]
            top = max(ns) if ns else None
            out = []
            for c in hdr:
                c2 = c.replace(" ", "")
                if re.search(r"\(E\)|추정|예상", c2):
                    out.append(None)
                    continue
                m = re.search(r"(20\d\d)", c2)
                y = int(m.group(1)) if m else None
                if y is None:
                    k = re.search(r"제(\d+)기", c2)
                    y = cy - (top - int(k.group(1))) if (k and top) else None
                if y is None or y > cy:
                    out.append(None)
                    continue
                q = ".3Q" if "3분기" in c2 else (".2Q" if "반기" in c2 else (".1Q" if "1분기" in c2 else ""))
                if q and re.search(r"전\s*(반|분)기|누적\)?$", c2) and False:
                    q = q
                out.append(f"{y}{q}")
            return out

        def scan(keys):
            best, best_score = None, None
            for i, t in enumerate(self.tables):
                rows = t["rows"]
                if len(rows) < 3 or len(rows[0]) < 3:
                    continue
                per = periods(rows[0][1:])
                if sum(1 for x in per if x) < 2:
                    continue
                near = " ".join(" ".join(" ".join(x) for x in self.tables[j]["rows"][:2]) for j in range(max(0, i - 2), i + 1))
                got = {}
                for r in rows[1:]:
                    lab = re.sub(r"[\s\[\]]", "", r[0] if r else "")
                    key = next((k for k, pat in keys.items() if re.match(pat, lab)), None)
                    if not key or key in got:
                        continue
                    vals = [_to_num(c.replace("△", "-")) if c not in ("-", "") else None for c in r[1:]]
                    if not any(v is not None for v in vals):
                        continue
                    big = max(abs(v) for v in vals if v is not None)
                    um = re.findall(r"단위\s*[:：]?\s*(백만원|천원|원)", near)
                    if um:
                        unit = {"백만원": 1e6, "천원": 1e3, "원": 1}[um[-1]]
                    else:
                        unit = 1 if big >= 1e8 else (1e6 if ("백만원" in near or big < 1e7) else 1e3)
                    got[key] = {p: v * unit / 1e8 for p, v in zip(per, vals) if p and v is not None}
                if not got:
                    continue
                latest = max(p for d in got.values() for p in d)
                score = (len(got), latest)
                if best_score is None or score >= best_score:
                    best, best_score = got, score
            return best or {}
        bs, is_ = scan(bs_keys), scan(is_keys)
        allp = {p for d in list(bs.values()) + list(is_.values()) for p in d}
        if not allp:
            return {}
        interim = sorted([p for p in allp if "." in p], reverse=True)[:1]
        fys = sorted([p for p in allp if "." not in p], reverse=True)[:2]
        cols = interim + fys
        rows = {}
        for k in list(bs_keys) + list(is_keys):
            src = bs.get(k) or is_.get(k)
            if src:
                rows[k] = [src.get(c) for c in cols]
        return {"cols": cols, "rows": rows}

    def ksic_name(self, code):
        """신고서 본문에서 'OOO업(M70113)' 형태로 적힌 업종명"""
        if not code:
            return None
        m = re.search(r"([가-힣][가-힣·,\s및]{2,40}업)\s*\(\s*[A-Z]?\s*" + re.escape(str(code)) + r"\s*\)", self.text)
        if m:
            name = m.group(1)
            for mk in ("있는 ", "속한 ", "속하는 ", "분류상 ", "상 "):
                if mk in name:
                    name = name.split(mk)[-1]
            return name.strip(" '\"")
        m = re.search(r"\(\s*[A-Z]?\s*" + re.escape(str(code)) + r"\s*\)\s*([가-힣][가-힣·,\s및]{2,40}업)", self.text)
        return m.group(1).strip() if m else None

    def forecast(self):
        """수요예측 일시 (정정 시 마지막 값)"""
        found = None
        for t in self.tables:
            for r in t["rows"]:
                if r and re.search(r"수요예측\s*(일시|기간|일$|예정일|접수기간)", r[0]):
                    txt = re.sub(r"(?<=\d)\s+(?=\d)", "", " ".join(r[1:3]))
                    ds = re.findall(r"(20\d\d)\s*[년.]\s*(\d{1,2})\s*[월.]\s*(\d{1,2})", txt)
                    if ds:
                        out = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in ds[:2]]
                        found = out if len(out) == 2 else out * 2
        return found

    # ---------- 공모가 관련 (구버전 휴리스틱, 요약표 없을 때 보조) ----------
    def band(self):
        m = re.search(r"(?:희망\s*공모\s*가액|공모\s*희망\s*가액|희망\s*공모가)[^0-9]{0,40}"
                      rf"({NUM})\s*원?\s*[~∼-]\s*({NUM})\s*원", self.text)
        if m:
            return [int(_to_num(m.group(1))), int(_to_num(m.group(2)))]
        return None

    def final_price(self):
        m = re.search(rf"확정\s*공모\s*가액[^0-9]{{0,30}}({NUM})\s*원", self.text)
        return int(_to_num(m.group(1))) if m else None

    def per_share_value(self):
        for t in self.tables:
            for r in t["rows"]:
                head = " ".join(r[:2])
                if "주당" in head and "평가" in head:
                    nums = [_to_num(c) for c in r[1:] if _to_num(c)]
                    if nums:
                        return nums[-1]
        m = re.search(rf"주당\s*평가\s*가액[^0-9]{{0,30}}({NUM})\s*원", self.text)
        return _to_num(m.group(1)) if m else None

    def discount(self):
        m = re.search(rf"할인율[^0-9%]{{0,40}}({NUM})\s*%\s*[~∼-]\s*({NUM})\s*%", self.text)
        return f"{m.group(1)}% ~ {m.group(2)}%" if m else None

    def method_and_multiple(self):
        """'적용 PER 26.76배' 같은 주관사 적용 멀티플"""
        for meth in METHODS:
            pat = rf"(?:적용|평균|최종)\s*{re.escape(meth)}[^0-9]{{0,25}}({NUM})\s*배"
            m = re.search(pat, self.text)
            if m:
                return meth, _to_num(m.group(1))
        for t in self.tables:
            for r in t["rows"]:
                h = r[0]
                for meth in METHODS:
                    if meth in h and any(k in h for k in ("적용", "평균")):
                        nums = [_to_num(c) for c in r[1:] if _to_num(c)]
                        if nums:
                            return meth, nums[-1]
        return None, None

    def peers(self, listed_names, method=None):
        """주관사 최종 유사기업 표 → [{'name','multiple'}]
        listed_names: 상장사 이름 집합(DART 고유번호 목록). 첫 열이 상장사명과
        가장 많이 맞는 표를 고르고, 그중 '최종'/'선정'이 제목에 있으면 가산점."""
        best, best_score = None, 0
        for t in self.tables:
            rows = t["rows"]
            hdr = " ".join(rows[0]) + " " + " ".join(rows[1] if len(rows) > 1 else [])
            if method and method not in hdr and method not in t["title"]:
                continue
            names = []
            for r in rows:
                for c in r[:2]:
                    nm = re.sub(r"\(.*?\)|㈜|주식회사", "", c).strip()
                    if nm in listed_names:
                        names.append((nm, r))
                        break
            score = len(names) + (2 if re.search("최종|선정", t["title"]) else 0)
            if len(names) >= 2 and score > best_score:
                best, best_score = names, score
        if not best:
            return []
        out = []
        for nm, r in best:
            nums = [_to_num(c) for c in r if _to_num(c) is not None]
            # 행의 마지막 숫자를 멀티플로 간주(표 대부분이 '…, PER' 순서)
            out.append({"name": nm, "ua_multiple": nums[-1] if nums else None})
        return out

    # ---------- 수요예측 결과 (정정신고서/투자설명서/발행실적보고서) ----------
    def _find_table(self, must_header, must_row=None):
        for t in self.tables:
            flat = " ".join(" ".join(r) for r in t["rows"][:3])
            if all(k in flat for k in must_header):
                if must_row is None or any(must_row in r[0] for r in t["rows"]):
                    return t["rows"]
        return None

    @staticmethod
    def _col(rows, key):
        for r in rows[:3]:
            for i, c in enumerate(r):
                if key in c:
                    return i
        return None

    def demand_stats(self):
        """수요예측 참여내역: 건수/수량 (합계, 운용사(집합))"""
        rows = self._find_table(["운용사"], "건수") or self._find_table(["운용사", "합계"])
        if not rows:
            return {}
        ci_am, ci_tot = self._col(rows, "운용사"), self._col(rows, "합계")
        out = {}
        for r in rows:
            key = r[0]
            def g(i):
                return _to_num(r[i]) if i is not None and i < len(r) else None
            if "건수" in key:
                out["am_count"], out["inst_count"] = g(ci_am), g(ci_tot)
            elif "수량" in key and "am_qty" not in out:
                out["am_qty"], out["total_qty"] = g(ci_am), g(ci_tot)
        return {k: v for k, v in out.items() if v is not None}

    def lockup_stats(self):
        """의무보유확약기간별 참여내역 → 운용사 확약수량 + 기간별 배정수량"""
        rows = self._find_table(["확약"], "6개월") or self._find_table(["확약"], "3개월")
        if not rows:
            return {}
        ci_am, ci_tot = self._col(rows, "운용사"), self._col(rows, "합계")
        am_lock, alloc = 0, {}
        for r in rows:
            k = r[0]
            for p in ("15일", "1개월", "3개월", "6개월"):
                if p in k:
                    if ci_am is not None and ci_am < len(r):
                        am_lock += _to_num(r[ci_am]) or 0
                    if ci_tot is not None and ci_tot < len(r):
                        alloc[p] = _to_num(r[ci_tot])
        return {"am_lock_qty": am_lock or None, "lock_by_period": alloc}

    def float_table(self):
        """상장 후 유통가능물량 표 중 기간별 확약 해제 물량(기존주주 자발적 확약 포함)"""
        out = {}
        for t in self.tables:
            flat = " ".join(" ".join(r) for r in t["rows"])
            if "유통" not in flat or "확약" not in flat:
                continue
            for r in t["rows"]:
                k = " ".join(r[:2])
                for p in ("1개월", "3개월", "6개월"):
                    if p in k and "확약" in k:
                        nums = [_to_num(c) for c in r[1:] if _to_num(c)]
                        big = [n for n in nums if n and n >= 1000]  # 주식수만
                        if big:
                            out[p] = out.get(p, 0) + big[0]
            if out:
                break
        return out

    def total_shares_after(self):
        m = re.search(rf"공모\s*후\s*(?:발행)?\s*주식\s*(?:총)?수[^0-9]{{0,30}}({NUM})\s*주", self.text)
        return int(_to_num(m.group(1))) if m else None

    def business_summary(self, n=200):
        """II. 사업의 내용 - 1. 사업의 개요 에서 회사 소개 문장"""
        for m in re.finditer(r"사업의\s*개요", self.text):
            seg = self.text[m.end():m.end() + 9000]
            for k in re.finditer(r"(당사|동사)는\s", seg):
                sent = re.split(r"(?<=다)\.\s|(?<=다)\.$", seg[k.start():k.start() + 600])[0]
                if 30 <= len(sent) <= 400 and re.search("영위|전문|기업|주력|개발|제조|공급|서비스|플랫폼", sent) \
                        and not re.search("증권신고서|정정|기재사항|용어", sent[:60]):
                    return sent[:n] + ("…" if len(sent) > n else "")
        return None
