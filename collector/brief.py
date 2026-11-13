"""보고서 서술 작성을 위한 신고서 요약본(brief) 생성 → debug/briefs/<이름>.txt

사람(또는 Claude)이 읽고 투자포인트·리스크 등 판단 문장을 쓰기 위한 자료.
실행: DUMP_NAMES="__all__" python -m collector.brief
"""
import json
import os
import re
from pathlib import Path

from .dart import Dart
from .filing_parser import Filing

ROOT = Path(__file__).resolve().parent.parent


def clip(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


def section(text, start_pat, n=2500):
    m = None
    for x in re.finditer(start_pat, text):
        seg = text[x.end():x.end() + 200]
        if not re.search(r"정정|기재사항|\(주\d+\)", seg[:80]):
            m = x
            break
    return clip(text[m.end():m.end() + n], n) if m else ""


def brief(d, ipo):
    out = [f"# {ipo['name']} | {ipo.get('market')} | 특례={ipo.get('special')} | KSIC {ipo.get('ksic')} {ipo.get('ksic_name')}",
           f"주관사 {ipo.get('underwriter')} | 밴드 {ipo.get('band')} | 확정가 {ipo.get('final_price')} | 공모주식 {ipo.get('shares_offered')} "
           f"| 구주 {ipo.get('sale_shares')} | 상장예정주식 {ipo.get('total_shares_after')}",
           f"일정 수요예측 {ipo.get('forecast')} 청약 {ipo.get('subscription')} 상장 {ipo.get('listing')}",
           f"주관사 평가: {ipo.get('ua_method')} {ipo.get('ua_multiple')}배, 주당 {ipo.get('ua_value')}원, 할인율 {ipo.get('discount')}, "
           f"적용이익 {round((ipo.get('ua_base') or 0) / 1e8, 1)}억, 추정 {json.dumps(ipo.get('ua_est'), ensure_ascii=False)}",
           f"주관사 peer: {[(p['name'], p['ua_multiple']) for p in ipo.get('peers_ua') or []]}",
           f"자금사용: {ipo.get('use_of_funds')}",
           f"재무(억원): {json.dumps(ipo.get('fin'), ensure_ascii=False)}",
           f"유통: {[(x['label'], round((x['ratio'] or 0) * 100, 2)) for x in ipo.get('float_schedule') or []]}", ""]

    out.append("## 사업의 개요")
    m = re.search(r"(?:당사|동사)는\s*20\d\d년\s*\d{1,2}월[^.]{0,80}설립", d.text)  # 설립 문장부터 (용어집 회피)
    bs = d.business_summary(n=60) or ""
    k = m.start() if m else (d.text.find(bs.rstrip("…")[:40]) if bs else -1)
    out.append(clip(d.text[k:k + 3500], 3500) if k >= 0 else section(d.text, r"사업의\s*개요", 3000))

    out.append("\n## 핵심투자위험 (항목 제목)")
    for t in d.tables:
        if "핵심투자위험" in t["title"] and len(t["rows"]) >= 3:
            for r in t["rows"][1:]:
                heads = re.findall(r"(?<![가-힣A-Za-z0-9])([가-하]\.\s[^.。]{4,90}?(?:위험|우려|가능성|한계|불확실성|부담|관련))", " ".join(r[1:]))
                out.append(f"- {r[0]}: " + " / ".join(dict.fromkeys(heads)))
            break

    out.append("\n## 주주별 취득단가·의무보유")
    for t in d.tables:
        hdr = " ".join(" ".join(r) for r in t["rows"][:2])
        if "취득" in hdr and ("단가" in hdr or "가격" in hdr) and len(t["rows"]) > 3:
            for r in t["rows"][:16]:
                out.append(" | ".join(clip(c, 40) for c in r[:8]))
            break

    out.append("\n## 배정 (그룹별)")
    for t in d.tables:
        labs = " ".join(r[0] for r in t["rows"] if r)
        if re.search("일반청약자|일반투자자", labs) and re.search("기관투자자", labs) and \
                any("%" in c for r in t["rows"] for c in r):
            for r in t["rows"][:10]:
                out.append(" | ".join(clip(c, 30) for c in r[:7]))
            break

    out.append("\n## 회사 추정 손익")
    for t in d.tables:
        hdr = " ".join(t["rows"][0])
        if re.search(r"20\d\d\s*(년)?\s*\(?E\)?|추정", hdr) and \
                any(re.match(r"(매출|영업수익|영업이익|당기순이익)", r[0].replace(" ", "")) for r in t["rows"] if r):
            for r in t["rows"][:10]:
                out.append(" | ".join(clip(c, 14) for c in r[:10]))
            break

    out.append("\n## 추정 근거/달성 관련 서술")
    out.append(section(d.text, r"추정\s*(?:손익|실적)의?\s*(?:근거|산출)", 1500))
    out.append("\n## 비교기업 선정 사유")
    out.append(section(d.text, r"(?:유사|비교)(?:기업|회사|대상회사)\s*선정\s*(?:과정|요약|기준)", 1500))
    return "\n".join(out)


def main():
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    names = {n.strip() for n in os.environ.get("DUMP_NAMES", "__all__").split(",") if n.strip()}
    dart = Dart()
    out_dir = ROOT / "debug" / "briefs"
    out_dir.mkdir(parents=True, exist_ok=True)
    for ipo in state["pipeline"]:
        if ipo.get("spac") or not ipo.get("corp_code"):
            continue
        if "__all__" not in names and ipo["name"] not in names:
            continue
        # 정정신고서는 바뀐 부분만 있으므로 가장 충실한(표가 많은) 지분증권 신고서를 고름
        fs = [f for f in dart.company_filings(ipo["corp_code"], days=200)
              if "증권신고서" in f["report_nm"] and "지분증권" in f["report_nm"]]
        fs.sort(key=lambda f: f["rcept_no"], reverse=True)
        best = None
        for f in fs[:4]:
            try:
                d = Filing(dart.document(f["rcept_no"]))
            except Exception as e:
                print("  fail", f["rcept_no"], e)
                continue
            if best is None or len(d.tables) > len(best.tables):
                best = d
            if len(d.tables) > 500:
                break
        if best is None:
            continue
        txt = brief(best, ipo)
        (out_dir / f"{ipo['name']}.txt").write_text(txt, encoding="utf-8")
        print("brief", ipo["name"], len(txt), flush=True)


if __name__ == "__main__":
    main()
