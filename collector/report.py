"""종목별 '적정가 산출 근거' PDF 생성 → site/reports/<corp_code>.pdf

실행: python -m collector.report   (data/state.json 을 읽어서 생성)
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "state.json"
OUT_DIR = ROOT / "site" / "reports"


def e(x):
    return html.escape("" if x is None else str(x))


def n(v, d=0):
    if v is None or v == "":
        return "–"
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return e(v)


def pct(v, d=1):
    return "–" if v is None else f"{v * 100:.{d}f}%"


def rng(a):
    if not a:
        return "–"
    return a[0][5:].replace("-", "/") + (" ~ " + a[1][5:].replace("-", "/") if a[1] != a[0] else "")


CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm;
  @bottom-right { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; } }
body { font-family: "NanumGothic", "Noto Sans CJK KR", sans-serif; font-size: 9pt; color: #111; line-height: 1.45; }
h1 { font-size: 16pt; margin: 0 0 2px; }
.sub { color: #666; font-size: 8.5pt; margin-bottom: 12px; }
h2 { font-size: 11pt; margin: 16px 0 6px; padding-bottom: 3px; border-bottom: 1.5px solid #111; }
table { width: 100%; border-collapse: collapse; margin: 4px 0; }
th, td { border-bottom: 0.5px solid #ccc; padding: 4px 5px; text-align: right; vertical-align: top; }
th { background: #f3f3f1; font-weight: bold; font-size: 8.5pt; }
td.l, th.l { text-align: left; }
.kv td { text-align: left; } .kv td.k { color: #555; width: 18%; background: #fafaf8; }
.hero { display: flex; gap: 10px; margin: 6px 0; }
.box { flex: 1; border: 1px solid #ddd; border-radius: 4px; padding: 7px 9px; }
.box b { display: block; font-size: 7.5pt; color: #666; font-weight: normal; }
.box span { font-size: 13pt; font-weight: bold; }
.ex { color: #999; }
.note { font-size: 8pt; color: #444; background: #f6f6f4; padding: 6px 8px; border-radius: 3px; margin-top: 6px; }
.f { font-family: monospace; font-size: 8.5pt; background: #f6f6f4; padding: 5px 8px; margin: 4px 0; }
"""


def formula(p, v):
    m = v["method"]
    if m == "EV/EBITDA":
        return (f"주당가치 = 주관사 주당평가가액 {n(p.get('ua_value'))}원 + 적용 EBITDA {n((p.get('ua_base') or 0) / 1e8)}억원"
                f" × (재산정 {v['multiple_mean']}배 − 주관사 {p.get('ua_multiple')}배) ÷ {n(p.get('ua_shares'))}주")
    return f"주당가치 = 적용 이익(현가) × 비교기업 평균 {m} {v['multiple_mean']}배 ÷ 적용주식수 {n(p.get('ua_shares'))}주"


def render(p):
    v = p.get("our_valuation") or {}
    m = v.get("method") or p.get("ua_method") or "PER"
    band = p.get("band")
    parts = [f"<h1>{e(p['name'])} 공모가 적정성 검토</h1>",
             f"<div class='sub'>작성 기준일 {date.today().isoformat()} · 자료: DART 증권신고서(접수번호 {e(p.get('rcept_no'))}), "
             f"비교기업 주가 네이버금융, 재무 DART · 자동 산출 자료이므로 원문과 함께 확인 바랍니다</div>"]

    # 1. 공모 개요
    parts.append("<h2>1. 공모 개요</h2><table class='kv'>")
    rows = [("시장 / 특례", f"{e(p.get('market') or '–')} / {'특례상장' if p.get('special') else '일반'}"),
            ("대표주관사", e(p.get("underwriter") or "–")),
            ("일정", f"수요예측 {rng(p.get('forecast'))} · 청약 {rng(p.get('subscription'))} · 상장 {e((p.get('listing') or '–')[5:])}"),
            ("희망밴드 / 확정가", f"{n(band[0]) + ' ~ ' + n(band[1]) + '원' if band else '–'} / {n(p.get('final_price'))}원"),
            ("공모주식수", f"{n(p.get('shares_offered'))}주"),
            ("사업 내용", e(p.get("business") or "–"))]
    parts += [f"<tr><td class='k'>{k}</td><td>{val}</td></tr>" for k, val in rows] + ["</table>"]

    # 2. 주관사 평가
    est = p.get("ua_est")
    parts.append("<h2>2. 주관사 평가 (증권신고서 공모가 산정 요약표)</h2><table class='kv'>")
    rows = [("평가 지표", f"{e(p.get('ua_method'))} {n(p.get('ua_multiple'), 2)}배"),
            ("적용 이익(①)", f"{n((p.get('ua_base') or 0) / 1e8, 1)}억원"
             + (f" — 추정이익 {', '.join(n(x / 1e8) + '억' for x in est['fv'])}을 연 {est['rate'] * 100:.0f}%로 현가 할인" if est else " (실적 기준)")),
            ("적용 주식수", f"{n(p.get('ua_shares'))}주"),
            ("주당 평가가액", f"{n(p.get('ua_value'))}원"),
            ("공모가 할인율", e(p.get("discount") or "–"))]
    parts += [f"<tr><td class='k'>{k}</td><td>{val}</td></tr>" for k, val in rows] + ["</table>"]

    if not v.get("peers"):
        parts.append("<div class='note'>비교기업 표를 읽지 못해 재산정하지 않았습니다.</div>")
        return "".join(parts)

    # 3. 재산정 결과
    val = v.get("value")
    vr = v.get("value_range")
    up = (val / band[1] - 1) if val and band else None
    parts.append("<h2>3. 재산정 결과</h2><div class='hero'>"
                 f"<div class='box'><b>재산정 주당가치</b><span>{n(vr[0]) + ' ~ ' + n(vr[1]) if vr else n(val)}원</span></div>"
                 f"<div class='box'><b>적용 {e(m)} (평균 / 중앙값)</b><span>{v.get('multiple_mean')}배</span> / {v.get('multiple_median')}배</div>"
                 f"<div class='box'><b>희망밴드 상단 대비</b><span>{'–' if up is None else f'{up:+.1%}'}</span></div>"
                 f"<div class='box'><b>밴드의 할인율(상단~하단)</b><span>{pct(v['band_discount'][0]) + ' ~ ' + pct(v['band_discount'][1]) if v.get('band_discount') else '–'}</span></div></div>")
    parts.append(f"<div class='f'>{e(formula(p, v))}</div>")
    if v.get("values") and len(v["values"]) > 1 and est:
        parts.append("<table><tr><th class='l'>시나리오</th><th>추정이익 현가</th><th>주당가치</th></tr>")
        shares = p.get("ua_shares") or 1
        for k, x in v["values"].items():
            pvv = x / v["multiple_mean"] * shares if v.get("multiple_mean") else None
            parts.append(f"<tr><td class='l'>{e(k)}</td><td>{n((pvv or 0) / 1e8, 1)}억원</td><td>{n(x)}원</td></tr>")
        parts.append(f"<tr><td class='l ex'>(참고) 주관사 {est['rate'] * 100:.0f}%</td><td class='ex'>{n((p.get('ua_base') or 0) / 1e8, 1)}억원</td>"
                     f"<td class='ex'>{n(v['multiple_mean'] * (p.get('ua_base') or 0) / shares)}원</td></tr></table>")

    # 4. 비교기업
    key = {"PER": "ltm_ni_eok", "EV/EBITDA": "ebitda_eok", "PBR": "equity_eok"}.get(m)
    lab = {"PER": "LTM 지배순이익", "EV/EBITDA": "LTM EBITDA", "PBR": "자본"}.get(m, "기준값")
    parts.append(f"<h2>4. 비교기업 재검토</h2><table><tr><th class='l'>기업</th><th>구분</th><th>61일 평균가</th>"
                 f"<th>시가총액(억)</th><th>{lab}(억)</th><th>기준기간</th><th>주관사 {e(m)}</th><th>재산정 {e(m)}</th><th class='l'>판단</th></tr>")
    for q in v["peers"]:
        cls = "" if q.get("kept") else " class='ex'"
        parts.append(f"<tr{cls}><td class='l'>{e(q['name'])}</td><td>{e(q.get('origin'))}</td><td>{n(q.get('avg61'))}</td>"
                     f"<td>{n(q.get('mcap_eok'))}</td><td>{n(q.get(key)) if key else '–'}</td><td>{e(q.get('fin_basis') or '–')}</td>"
                     f"<td>{n(q.get('ua_multiple'), 2)}</td><td>{n((q.get('multiples') or {}).get(m), 2)}</td>"
                     f"<td class='l'>{'✓ ' if q.get('kept') else '✕ '}{e(q.get('reason'))}</td></tr>")
    parts.append("</table>")

    # 5. PER 병행
    pc = v.get("per_check")
    if pc:
        parts.append("<h2>5. PER 기준 병행 산정</h2>")
        if pc.get("value"):
            parts.append(f"<div class='f'>주당가치 = {e(pc['basis'])} 지배순이익 {n(pc['ni'] / 1e8, 1)}억원 × 비교기업 평균 PER "
                         f"{pc['multiple_mean']}배 ÷ {n(p.get('ua_shares'))}주 = <b>{n(pc['value'])}원</b></div><table>"
                         "<tr><th class='l'>기업</th><th>PER</th><th class='l'>판단</th></tr>")
            for q in pc["peers"]:
                cls = "" if q.get("kept") else " class='ex'"
                parts.append(f"<tr{cls}><td class='l'>{e(q['name'])}</td><td>{n(q.get('PER'), 2)}</td>"
                             f"<td class='l'>{'✓ ' if q['kept'] else '✕ '}{e(q.get('reason'))}</td></tr>")
            parts.append("</table>")
        else:
            parts.append(f"<div class='note'>{e(pc.get('reason'))}</div>")

    # 6. 기준
    parts.append("<h2>산정 기준</h2><div class='note'>"
                 f"· {e(v.get('price_basis') or '')}<br>· {e(v.get('rationale') or '')}"
                 + (f"<br>· {e(v.get('note'))}" if v.get("note") else "")
                 + "<br>· 해외·비상장 비교기업은 국내 시세가 없어 재산정에서 제외"
                 "<br>· 신고서 수치는 자동 추출값이며, 비교기업 재무는 DART 정기보고서 기준 최근 4개 분기 합산</div>")
    return "".join(parts)


def main():
    from weasyprint import HTML
    state = json.loads(STATE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    for p in state["pipeline"]:
        if p.get("spac") or not p.get("corp_code") or not p.get("ua_method"):
            continue
        doc = f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{render(p)}</body></html>"
        HTML(string=doc).write_pdf(OUT_DIR / f"{p['corp_code']}.pdf")
        made += 1
    print(f"PDF {made}건 생성")


if __name__ == "__main__":
    main()
