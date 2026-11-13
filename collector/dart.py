"""DART OpenAPI 클라이언트 (필요한 엔드포인트만).

환경변수 DART_API_KEY 필요. 하루 호출 한도(개인키 기준 20,000회) 안에서 동작하도록
원문/고유번호는 data/cache 에 캐시한다.
"""
import io
import re
import json
import os
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE = "https://opendart.fss.or.kr/api"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


class DartError(RuntimeError):
    pass


class Dart:
    def __init__(self, key=None):
        self.key = key or os.environ.get("DART_API_KEY")
        if not self.key:
            raise DartError("DART_API_KEY 환경변수가 없습니다")
        self.s = requests.Session()

    # ---------- 공통 ----------
    def _get(self, path, **params):
        params["crtfc_key"] = self.key
        r, err = None, None
        for attempt in range(4):  # DART 응답이 느릴 때가 많아 타임아웃·오류 시 재시도
            try:
                r = self.s.get(f"{BASE}/{path}", params=params, timeout=(10, 60))
                if r.status_code == 200:
                    break
            except requests.RequestException as e:
                err = e
            time.sleep(3 * (attempt + 1))
        if r is None:
            raise err
        r.raise_for_status()
        if path.endswith(".json"):
            j = r.json()
            st = j.get("status")
            if st == "013":  # 조회된 데이터 없음
                return {}
            if st != "000":
                raise DartError(f"{path} {st} {j.get('message')}")
            return j
        return r.content

    # ---------- 고유번호 ----------
    def corp_codes(self, max_age_days=7):
        """{회사명: {'corp_code','stock_code'}} — 상장사 이름 매칭용"""
        f = CACHE / "corpcode.json"
        if f.exists() and time.time() - f.stat().st_mtime < max_age_days * 86400:
            return json.loads(f.read_text(encoding="utf-8"))
        raw = self._get("corpCode.xml")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read(z.namelist()[0])
        out = {}
        for el in ET.fromstring(xml).iter("list"):
            name = (el.findtext("corp_name") or "").strip()
            code = (el.findtext("corp_code") or "").strip()
            stock = (el.findtext("stock_code") or "").strip()
            # 같은 이름이 여럿이면 상장사 우선
            if name not in out or (stock and not out[name]["stock_code"]):
                out[name] = {"corp_code": code, "stock_code": stock}
        f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    # ---------- 공시 검색 ----------
    def list_filings(self, bgn, end, pblntf_ty=None, detail_ty=None, corp_code=None, corp_cls=None):
        rows, page = [], 1
        while True:
            p = dict(bgn_de=bgn, end_de=end, page_no=page, page_count=100)
            if corp_cls:
                p["corp_cls"] = corp_cls
            if pblntf_ty:
                p["pblntf_ty"] = pblntf_ty
            if detail_ty:
                p["pblntf_detail_ty"] = detail_ty
            if corp_code:
                p["corp_code"] = corp_code
            j = self._get("list.json", **p)
            rows += j.get("list", [])
            if not j or page >= int(j.get("total_page", 1)):
                break
            page += 1
        return rows

    def ipo_filings(self, days=200):
        """최근 N일 증권신고서(지분증권) 중 비상장사(corp_cls=E) = 신규상장 후보"""
        end = date.today()
        out = []
        # list.json 은 기간 3개월 제한이 있어 90일 단위로 나눠 조회
        cur = end - timedelta(days=days)
        while cur < end:
            nxt = min(cur + timedelta(days=89), end)
            out += self.list_filings(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"), pblntf_ty="C", corp_cls="E")
            cur = nxt + timedelta(days=1)
        out = [r for r in out if r.get("corp_cls") == "E"]
        # 철회: 마지막 증권신고서 이후에 철회신고서가 있으면 제외
        last_reg, last_wd = {}, {}
        for r in out:
            nm = r.get("report_nm", "")
            if "철회" in nm:
                last_wd[r["corp_code"]] = max(last_wd.get(r["corp_code"], ""), r["rcept_no"])
            elif "증권신고서" in nm and "지분증권" in nm:
                last_reg[r["corp_code"]] = max(last_reg.get(r["corp_code"], ""), r["rcept_no"])
        self.withdrawn = {r["corp_name"] for r in out
                          if r["corp_code"] in last_wd and last_wd[r["corp_code"]] > last_reg.get(r["corp_code"], "")}
        self.withdrawn_detail = sorted({(r["corp_name"], r["report_nm"], r["rcept_dt"]) for r in out
                                        if r["corp_name"] in self.withdrawn and "철회" in r["report_nm"]})
        return [r for r in out if "증권신고서" in r.get("report_nm", "") and "지분증권" in r.get("report_nm", "")
                and r["corp_name"] not in self.withdrawn]

    def company_filings(self, corp_code, days=400):
        end = date.today()
        return self.list_filings((end - timedelta(days=days)).strftime("%Y%m%d"),
                                 end.strftime("%Y%m%d"), pblntf_ty="C", corp_code=corp_code)

    # ---------- 증권신고서 주요정보(지분증권) ----------
    def equity_summary(self, corp_code, bgn, end):
        j = self._get("estkRs.json", corp_code=corp_code, bgn_de=bgn, end_de=end)
        groups = {}
        for g in j.get("group", []) if j else []:
            groups[g.get("title", "")] = g.get("list", [])
        return groups

    # ---------- 원문 ----------
    def document(self, rcept_no):
        f = CACHE / f"doc_{rcept_no}.xml"
        if f.exists():
            return f.read_text(encoding="utf-8")
        raw = self._get("document.xml", rcept_no=rcept_no)
        if raw[:2] != b"PK":  # zip이 아니면 DART 오류 XML (예: 014 파일 없음, 020 한도초과)
            msg = raw[:300].decode("utf-8", "ignore")
            st = re.search(r"<status>(.*?)</status>", msg)
            me = re.search(r"<message>(.*?)</message>", msg)
            raise DartError(f"원문 {rcept_no}: {st and st.group(1)} {me and me.group(1)}")
        parts = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                b = z.read(n)
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        parts.append(b.decode(enc))
                        break
                    except UnicodeDecodeError:
                        continue
        txt = "\n".join(parts)
        f.write_text(txt, encoding="utf-8")
        return txt

    # ---------- 기업개황 ----------
    def company(self, corp_code):
        return self._get("company.json", corp_code=corp_code)

    # ---------- 재무 (peer 재산정용) ----------
    def fs_all(self, corp_code, year, reprt_code, fs_div="CFS"):
        j = self._get("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year),
                      reprt_code=reprt_code, fs_div=fs_div)
        return j.get("list", []) if j else []
