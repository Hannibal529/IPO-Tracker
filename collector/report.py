"""종목별 IPO 밸류에이션 보고서 PDF (팀 IPO 보고서 양식) → site/reports/<corp_code>.pdf

양식: 요약 → 공모개요 → 일정 → 수요예측 결과 → 투자포인트/리스크 → 기업개요 → 회사 추정 손익
      → 수급 → 밸류에이션(주관사) → 밸류에이션(당사)·민감도 → 최근 IPO 비교 → 재무 현황
판단이 필요한 칸(투자포인트, 산업매력도 등)은 '작성 필요'로 남긴다. 참여 의견은 넣지 않는다.
실행: python -m collector.report
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "state.json"
OUT_DIR = ROOT / "site" / "reports"
TODO = "<span class='todo'>작성 필요</span>"


def e(x):
    return html.escape("" if x is None else str(x))


def n(v, d=0):
    if v is None or v == "":
        return "-"
    try:
        x = float(v)
        x = x + (1e-9 if x >= 0 else -1e-9)  # 0.5 반올림(엑셀과 동일)
        q = 10 ** d
        x = int(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)
        return f"{x:,.{d}f}"
    except (TypeError, ValueError):
        return e(v)


def eok(won, d=0):
    return "-" if won is None else f"{won / 1e8:,.{d}f}억원"


def pct(v, d=1):
    return "-" if v is None else f"{v * 100:.{d}f}%"


def ymd(s):
    return s.replace("-", ".") if s else "-"


def rng(a):
    if not a:
        return "-"
    return ymd(a[0]) + ("~" + ymd(a[1]) if a[1] != a[0] else "")


def table(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head) if head else ""
    body = "".join("<tr>" + "".join(f"<td class='{'lab' if i == 0 else ''}'>{c}</td>" for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f"<table class='{cls}'>{'<tr>' + h + '</tr>' if h else ''}{body}</table>"


def kv(rows):
    return "<table class='kv'>" + "".join(f"<tr><td class='lab'>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"


CSS = """
@page { size: A4; margin: 15mm 16mm 15mm 16mm; }
body { font-family: "NanumGothic", "Noto Sans CJK KR", sans-serif; font-size: 9.5pt; color: #000; line-height: 1.5; }
h1 { text-align: center; font-size: 15pt; margin: 0; }
.date { text-align: right; font-weight: bold; font-size: 10pt; margin-bottom: 4px; }
h2 { font-size: 10.5pt; margin: 14px 0 4px; }
h2:before { content: "● "; }
ul { margin: 2px 0 2px 0; padding-left: 18px; } li { list-style: "-  "; margin: 1px 0; }
table { width: 100%; border-collapse: collapse; margin: 4px 0; font-size: 9pt; page-break-inside: avoid; }
th { background: #dce6f1; border: 0.6px solid #555; padding: 3px 4px; font-weight: bold; text-align: center; }
td { border: 0.6px solid #555; padding: 3px 5px; text-align: center; vertical-align: middle; }
td.lab { background: #fbe5d6; }
table.kv td.lab { background: #dce6f1; font-weight: bold; width: 32%; }
table.sum td { border-left: 0; border-right: 0; } table.sum td.lab { background: #dce6f1; font-weight: bold; width: 18%; }
.small { font-size: 7.5pt; color: #333; margin: 0; }
.todo { color: #c00; font-size: 8.5pt; }
.bold { font-weight: bold; }
td.b { font-weight: bold; }
"""


def grade_price(v, band):
    if not v or not band:
        return "-", "-"
    lo, hi = band
    if v >= hi:
        return "적정", "밴드 상단 이상"
    if v >= lo:
        return "상단 기준 고평가", "하단~중단 적정"
    return "고평가", "밴드 하단 미만"


def grade_float(r):
    if r is None:
        return "-"
    return "다소 부담" if r > 0.35 else ("보통" if r > 0.25 else "양호")


def render(p, state):
    v = p.get("our_valuation") or {}
    m = v.get("method") or p.get("ua_method") or "PER"
    band = p.get("band")
    lo, hi = band if band else (None, None)
    shares = p.get("ua_shares")
    listed = p.get("total_shares_after") or (p["float_schedule"][-1]["shares"] if p.get("float_schedule") else None)
    fl = p.get("float_schedule") or []
    fl0 = fl[0] if fl else {}
    val = v.get("value")
    est = p.get("ua_est")
    peers_names = "·".join(q["name"].split(" (")[0] for q in (p.get("peers_ua") or []))
    out = [f"<h1>{e(p['name'])} IPO 밸류에이션</h1><div class='date'>{date.today().strftime('%Y.%m.%d')}</div>"]

    # 요약
    g, gtxt = grade_price(val, band)
    fin = p.get("fin") or {}
    fr = fin.get("rows") or {}
    fin_txt = (f"자본총계 {n(fr['자본총계'][0])}억원, 부채총계 {n(fr['부채총계'][0])}억원 ({fin['cols'][0]})"
               if fr.get("자본총계") and fr.get("부채총계") else "")
    out.append("<h2>요약</h2>" + table(None, [
        ["공모가격", e(g), f"적정가 {n(val)}원 (밴드 {n(lo)}~{n(hi)}원)" if val else "재산정 불가"],
        ["수급", e(grade_float(fl0.get("ratio"))), f"상장일 유통 {pct(fl0.get('ratio'), 0)}" if fl0 else "-"],
        ["산업매력도", TODO, ""],
        ["성장성", TODO, ""],
        ["재무", TODO, e(fin_txt)],
    ], "sum"))
    if val:
        out.append(f"<ul><li class='bold'>당사 적정가 {n(val)}원 (공모밴드 {n(lo)}~{n(hi)}원 / 주관사 평가가액 {n(p.get('ua_value'))}원)</li></ul>")

    # 공모 개요
    off = p.get("shares_offered")
    sale = p.get("sale_shares") or 0
    method_txt = (f"신주 {pct((off - sale) / off, 0)}" + (f" / 구주매출 {pct(sale / off, 0)}" if sale else "")) if off else "-"
    uof = p.get("use_of_funds") or []
    uof_txt = ", ".join(f"{e(u['label'])} {u['amount'] / 1e8:,.0f}억원" for u in uof) if uof else TODO
    out.append("<h2>공모 개요</h2>" + kv([
        ("대표주관사", e(p.get("underwriter") or "-")),
        ("공모주식수", f"{n(off)}주"),
        ("공모방법", method_txt),
        ("밴드가", f"{n(lo)}원(하단) ~ {n(hi)}원(상단)" if band else "-"),
        ("공모금액", f"{n(off * lo / 1e8)}억원 ~ {n(off * hi / 1e8)}억원" if off and band else "-"),
        ("예상시가총액", f"{n(listed * lo / 1e8)}억원 ~ {n(listed * hi / 1e8)}억원" if listed and band else "-"),
        (f"단기 유통가능금액<br>({pct(fl0.get('ratio'), 2)})" if fl0 else "단기 유통가능금액",
         f"{n(fl0['shares'] * lo / 1e8)}억원 ~ {n(fl0['shares'] * hi / 1e8)}억원" if fl0 and band else "-"),
        ("그룹별 배정", TODO),
        ("자금 사용처", uof_txt),
    ]))

    # 일정
    out.append("<h2>일정</h2><ul>"
               f"<li>수요예측 : {rng(p.get('forecast'))}</li><li>공모청약일 : {rng(p.get('subscription'))}</li>"
               f"<li>납입일 : {ymd(p.get('payment'))}</li>"
               f"<li>상장예정일 : {ymd(p.get('listing')) if p.get('listing') else '미정'} "
               f"({e(p.get('market') or '')}{', 기술특례' if p.get('special') else ''})</li></ul>")

    # 수요예측 결과
    after = (p.get("forecast") or [None, None])[1]
    pend = f"{ymd(after)[5:]} 발표 후 기재" if after and after >= date.today().isoformat() else "-"
    lock = p.get("lock_ratio_38") or ((p.get("demand_stats") or {}).get("lock_ratio_applied"))
    out.append("<h2>수요예측 결과 (발표 후 업데이트)</h2>" + table(["구분", "결과"], [
        ["기관 경쟁률", e(p.get("competition") or pend)],
        ["확정공모가", f"{n(p.get('final_price'))}원" if p.get("final_price") else "-"],
        ["의무보유 확약 비율", pct(lock) if lock else "-"],
        ["일반청약 경쟁률", e(p.get("retail_competition") or "-")],
    ]))

    # 투자포인트 / 리스크
    risks = []
    if est:
        yrs = est.get("years") or []
        risks.append(f"공모가가 {yrs[0] + '~' + yrs[-1] if yrs else '미래'}년 추정 순이익에 근거함 (주관사 할인율 {est['rate'] * 100:.0f}%).")
    if fl0.get("ratio"):
        nxt = next((x for x in fl if x["label"] in ("1개월", "2개월")), None)
        risks.append(f"상장일 유통물량 {pct(fl0['ratio'], 2)}"
                     + (f", {nxt['label']} 후 {pct(nxt['ratio'], 2)}까지 증가함." if nxt else "임."))
    out.append(f"<h2>투자포인트</h2><ul><li>{TODO}</li></ul>"
               f"<h2>리스크</h2><ul>{''.join(f'<li>{e(x)}</li>' for x in risks)}<li>{TODO}</li></ul>")

    # 기업개요
    out.append(f"<h2>기업개요</h2><ul><li>{e(p.get('business') or '')}</li><li>{TODO}</li></ul>")

    # 회사 추정 손익
    if est and est.get("years"):
        out.append("<p class='bold'>회사 추정 순이익 (증권신고서, 단위: 억원)</p>"
                   + table(["구분"] + [f"{y}(E)" for y in est["years"]],
                           [["당기순이익"] + [n(x / 1e8) for x in est["fv"]]])
                   + "<p class='small'>*추정 순이익을 공모가 산정에 사용</p>")

    # 수급
    if fl:
        keep = {"상장일": "상장일", "1개월": "1개월 후", "2개월": "2개월 후", "3개월": "3개월 후",
                "6개월": "6개월 후", "12개월": "1년 후", "1년": "1년 후"}
        rows, seen = [], set()
        for x in fl:
            lab = keep.get(x["label"])
            if lab and lab not in seen:
                seen.add(lab)
                rows.append([lab, f"{n(x['shares'])}주", pct(x["ratio"], 2)])
        out.append("<h2>수급 (유통물량·오버행)</h2>" + table(["구분", "누적 유통가능 주식수", "비율"], rows)
                   + f"<ul><li>{TODO} (기존주주 취득단가, 주요 해제 물량)</li></ul>")

    # 밸류에이션(주관사)
    ua_lines = []
    if est:
        yrs = est.get("years") or []
        ua_lines.append(f"{yrs[0] + '~' + yrs[-1] + '년 ' if yrs else ''}추정 순이익을 {est['rate'] * 100:.0f}%로 할인하고, "
                        f"PEER({e(peers_names)}) {e(p.get('ua_method'))} {n(p.get('ua_multiple'), 2)}배를 적용함 → 주당 {n(p.get('ua_value'))}원.")
    else:
        ua_lines.append(f"적용 이익 {eok(p.get('ua_base'), 1)}에 PEER({e(peers_names)}) {e(p.get('ua_method'))} "
                        f"{n(p.get('ua_multiple'), 2)}배를 적용함 → 주당 {n(p.get('ua_value'))}원.")
    if band and shares and p.get("ua_base") and p.get("ua_method") in ("PER", "PSR", "PBR"):
        for lab, px in (("상단", hi), ("하단", lo)):
            cap = px * shares
            ua_lines.append(f"공모가 밴드 {lab} 기준 시총은 {n(cap / 1e8)}억원(주당 {n(px)}원), "
                            f"적용 {p['ua_method']}은 {cap / p['ua_base']:.2f}배 수준임.")
    out.append("<h2>밸류에이션 평가(주관사)</h2><ul>" + "".join(f"<li>{x}</li>" for x in ua_lines) + "</ul>")

    # 밸류에이션(당사)
    out.append("<h2>밸류에이션 평가(당사)</h2>")
    if not v or not val:
        out.append(f"<ul><li>{e(v.get('note') or '비교기업 표를 읽지 못해 재산정하지 않음')}</li></ul>")
    else:
        pb = next((q.get("multiples", {}).get("_per_basis") for q in v["peers"] if q.get("kept")), None)
        my = []
        my.append(f"피어 적정성: {TODO}")
        my.append(f"{e(pb or '')} 순이익과 3개월 평균주가로 다시 산정한 {m} {v['multiple_mean']:.2f}배를 적용함."
                  if m == "PER" else f"3개월 평균주가로 다시 산정한 {m} {v['multiple_mean']:.2f}배를 적용함.")
        if v.get("rate_base") is not None and est:
            my.append(f"할인율 {v['rate_base'] * 100:.0f}%를 적용함 (주관사 {est['rate'] * 100:.0f}%). {TODO}")
        if shares:
            my.append(f"적정 시총은 {n(val * shares / 1e8)}억원(주당 {n(val)}원)임.")
        my.append(f"적정 평가가액은 {n(val)}원으로 {e(gtxt)}" + (", 상단 기준 고평가임." if g == "상단 기준 고평가" else "임."))
        if v.get("multiple_ex_top") and v.get("top_peer"):
            top = next((q for q in v["peers"] if q["name"] == v["top_peer"]), {})
            exb = next((r.get("ex_top") for r in (v.get("sens") or []) if r["rate"] == v.get("rate_base")),
                       (v.get("sens") or [{}])[0].get("ex_top"))
            my.append(f"{m} 민감도: {e(v['top_peer'].split(' (')[0])}({m} {n(top.get('multiples', {}).get(m), 1)}배)을 제외하면 "
                      f"적정가는 {n(exb)}원임(아래 민감도 표 참고).")
        out.append("<ul>" + "".join(f"<li>{x}</li>" for x in my) + "</ul>")

        cmp_rows = [["PEER 그룹", e(peers_names), "동일" if all(q.get("origin") == "주관사" for q in v["peers"]) else "변경"],
                    [m, f"{n(p.get('ua_multiple'), 2)}배 (신고서)", f"{v['multiple_mean']:.2f}배 ({e(pb or 'LTM')}, 3개월 평균주가)"]]
        if est and v.get("rate_base") is not None:
            cmp_rows.append(["할인율", f"{est['rate'] * 100:.0f}%", f"{v['rate_base'] * 100:.0f}%"])
            cmp_rows.append(["순이익 현가", eok(p.get("ua_base"), 1), eok(v.get("pv_base"), 1)])
        if shares:
            cmp_rows.append(["시가총액", eok((p.get("ua_value") or 0) * shares), eok(val * shares)])
        cmp_rows.append(["<b>주당 가치</b>", f"<b>{n(p.get('ua_value'))}원</b>", f"<b>{n(val)}원</b>"])
        if hi:
            cmp_rows.append(["공모가 상단 대비", f"{(p.get('ua_value') or 0) / hi - 1:+.1%}", f"{val / hi - 1:+.1%}"])
        out.append(table(["구분", "주관사", "당사"], cmp_rows)
                   + "<p class='small'>*희석가능주식수(주식매수선택권 등) 포함된 밸류</p>"
                   + f"<p class='small'>*적용 주가는 {v.get('as_of', '')} 기준 최근 61영업일 평균값</p>")
        if v.get("sens"):
            exn = e((v.get("top_peer") or "").split(" (")[0])
            head = ["할인율 \\ " + m, f"{v['multiple_mean']:.2f}배 (주관사 {len([q for q in v['peers'] if q.get('kept')])}사)"]
            if v.get("multiple_ex_top"):
                head.append(f"{v['multiple_ex_top']:.2f}배 ({exn} 제외)")
            rows = []
            for r in v["sens"]:
                lab = (f"{r['rate'] * 100:.0f}%" + (" (기본)" if r["rate"] == v.get("rate_base") else "")) if r["rate"] is not None else "기본"
                rows.append([lab, f"{n(r['all'])}원"] + ([f"{n(r.get('ex_top'))}원"] if v.get("multiple_ex_top") else []))
            out.append("<p class='small'>[밸류 민감도: 주당 적정가]</p>" + table(head, rows))
        # 비교기업 상세
        prow = []
        for q in v["peers"]:
            mm = q.get("multiples") or {}
            prow.append([e(q["name"]), n(q.get("avg61")), n(q.get("mcap_eok")),
                         f"{n(mm.get('_per_ni_eok'))} ({e(mm.get('_per_basis') or '-')})" if m == "PER" else n(q.get("ebitda_eok")),
                         n(mm.get(m), 2), ("✓ " if q.get("kept") else "✕ ") + e(q.get("reason"))])
        out.append("<p class='small'>[비교기업 산정 내역]</p>"
                   + table(["기업", "3개월 평균가", "시총(억)", "순이익(억, 기준)" if m == "PER" else "EBITDA(억)", m, "판단"], prow))
        pc = v.get("per_check")
        if pc:
            out.append("<ul><li>" + (f"PER 기준 병행: {e(pc['basis'])} 지배순이익 {eok(pc['ni'], 1)} × 평균 PER {pc['multiple_mean']}배 "
                                     f"→ 주당 {n(pc['value'])}원" if pc.get("value") else f"PER 기준 병행: {e(pc.get('reason'))}")
                       + "</li></ul>")

    # 최근 IPO 비교
    recent = [d for d in (state.get("demand") or []) if d.get("inst_competition") and d.get("listing")]
    recent = sorted(recent, key=lambda d: d["listing"], reverse=True)[:3]
    if recent:
        rows = [[f"{e(d['name'])} ({d['listing'][2:7].replace('-', '.')})", f"{n(d['inst_competition'])}:1",
                 n(d.get("amount_eok")) + "억원" if d.get("amount_eok") else "-",
                 pct(d.get("am_lock_ratio") or d.get("lock_ratio_38"), 0) if (d.get("am_lock_ratio") or d.get("lock_ratio_38")) else "-",
                 ""] for d in recent]
        out.append("<h2>최근 IPO 비교</h2>" + table(["종목 (상장)", "기관 경쟁률", "공모금액", "확약 비율", "비고"], rows)
                   + f"<ul><li>{TODO} (동종 업종 IPO로 교체 권장)</li></ul>")

    # 재무 현황
    if fr:
        cols = fin["cols"]
        out.append("<h2>재무 현황</h2><p class='small' style='text-align:right'>(단위 : 억원)</p>"
                   + table(["구분"] + [e(c) for c in cols],
                           [[k if k != "당기순이익" else "당기순이익"] + [n(x) for x in vals] for k, vals in fr.items()]))
    out.append(f"<p class='small'>자료: DART 증권신고서(접수번호 {e(p.get('rcept_no'))}), 비교기업 주가 네이버금융·KRX, "
               "컨센서스 네이버증권(EPS 기준) · 자동 작성 초안이므로 원문 확인 후 사용</p>")
    return "".join(out)


def main():
    from weasyprint import HTML
    state = json.loads(STATE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    for p in state["pipeline"]:
        if p.get("spac") or not p.get("corp_code") or not p.get("ua_method"):
            continue
        doc = f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{render(p, state)}</body></html>"
        HTML(string=doc).write_pdf(OUT_DIR / f"{p['corp_code']}.pdf")
        made += 1
    print(f"PDF {made}건 생성")


if __name__ == "__main__":
    main()
