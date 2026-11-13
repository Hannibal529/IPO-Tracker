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
    s = str(s).replace(",", "").replace("배", "").replace("원", "").replace("주", "").strip()
    s = s.replace("(", "-").replace(")", "") if re.fullmatch(r"\([\d.]+\)", s) else s
    try:
        return float(s)
    except ValueError:
        m = re.search(NUM, s)
        return float(m.group().replace(",", "")) if m else None


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

    # ---------- 공모가 관련 ----------
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

    def business_summary(self, n=160):
        m = re.search(r"(?:회사의\s*개요|사업의\s*개요)\s*(.{40,600}?)(?:\.|다\.)", self.text)
        return (m.group(1)[:n] + "…") if m else None
