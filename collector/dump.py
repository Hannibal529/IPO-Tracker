"""파서 튜닝용: 실제 신고서에서 키워드가 있는 표와 문단만 뽑아 debug/ 에 저장.
실행: DUMP_NAMES="엠비디,아델" python -m collector.dump
"""
import json
import os
import re
from pathlib import Path

from .dart import Dart
from .filing_parser import Filing

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("유사", "PER", "EV/EBITDA", "PSR", "PBR", "평가가액", "할인", "수요예측", "확약", "유통", "비교")
SNIP = ("수요예측", "주당 평가가액", "평가가액", "할인율", "유사기업", "희망공모가", "상장예정", "청약기일")


def main():
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    names = [n.strip() for n in os.environ.get("DUMP_NAMES", "").split(",") if n.strip()]
    dart = Dart()
    out_dir = ROOT / "debug"
    out_dir.mkdir(exist_ok=True)
    for ipo in state["pipeline"]:
        if names and ipo["name"] not in names:
            continue
        if not ipo.get("rcept_no") or ipo.get("spac"):
            continue
        print("dump", ipo["name"], flush=True)
        try:
            doc = Filing(dart.document(ipo["rcept_no"]))
        except Exception as e:
            print("  fail", e)
            continue
        tables = []
        for i, t in enumerate(doc.tables):
            flat = t["title"] + " " + " ".join(" ".join(r) for r in t["rows"][:6])
            if any(k in flat for k in KEYS):
                tables.append({"i": i, "title": t["title"][:150], "rows": [r[:12] for r in t["rows"][:30]]})
        snips = {}
        for k in SNIP:
            snips[k] = [doc.text[max(0, m.start() - 120): m.start() + 220]
                        for m in list(re.finditer(re.escape(k), doc.text))[:4]]
        (out_dir / f"{ipo['name']}.json").write_text(json.dumps(
            {"name": ipo["name"], "rcept_no": ipo["rcept_no"], "n_tables": len(doc.tables),
             "tables": tables[:80], "snips": snips}, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()
