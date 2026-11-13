"""보고서 서술(data/narratives.json)이 필요한 종목 목록.
실행: python -m collector.nar_todo  → JSON 출력
- 서술이 없는 종목(new)
- 서술을 쓴 뒤 새 신고서/정정신고서가 접수된 종목(changed)
스팩은 제외.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def todo():
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    nf = ROOT / "data" / "narratives.json"
    nars = json.loads(nf.read_text(encoding="utf-8")) if nf.exists() else {}
    out = []
    for p in state["pipeline"]:
        if p.get("spac") or not p.get("corp_code"):
            continue
        nar = nars.get(p["corp_code"])
        if not nar:
            out.append({"name": p["name"], "corp_code": p["corp_code"], "rcept_no": p.get("rcept_no"), "reason": "new"})
        elif p.get("rcept_no") and nar.get("rcept_no") != p.get("rcept_no"):
            out.append({"name": p["name"], "corp_code": p["corp_code"], "rcept_no": p.get("rcept_no"),
                        "prev_rcept_no": nar.get("rcept_no"), "reason": "changed"})
    return out


if __name__ == "__main__":
    print(json.dumps(todo(), ensure_ascii=False, indent=1))
