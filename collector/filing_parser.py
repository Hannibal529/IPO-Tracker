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
                if "확정" in joined or "결정" in joined:
                    if len(nums) == 1 or "확정" in lab:
                        out["final_price"] = nums[-1] if nums else None
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
