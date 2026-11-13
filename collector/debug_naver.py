"""컨센서스 소스 점검용: python -m collector.debug_naver 000100"""
import io
import json
import sys

import pandas as pd
import requests

code = sys.argv[1] if len(sys.argv) > 1 else "000100"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

print("=== FnGuide")
try:
    r = requests.get(f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}", headers=H, timeout=20)
    print("status", r.status_code, "len", len(r.content), "enc", r.encoding)
    r.encoding = "utf-8"
    print("has 지배주주순이익:", "지배주주순이익" in r.text, "| Financial Highlight:", "Financial Highlight" in r.text)
    for k, df in enumerate(pd.read_html(io.StringIO(r.text))):
        cols = [" | ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
        if any("(E)" in c for c in cols):
            print("-- table", k, df.shape, cols[:10])
            lab = df.iloc[:, 0].astype(str)
            print(df[lab.str.contains("지배주주순이익|당기순이익|EPS", regex=True)].iloc[:, :10].to_string()[:1200])
            break
except Exception as e:
    print("ERR", type(e).__name__, e)

print("=== Naver mobile API")
for path in ("finance/annual", "finance/quarter"):
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/{path}", headers=H, timeout=20)
        print(path, "status", r.status_code, "len", len(r.content))
        j = r.json()
        fi = j.get("financeInfo") or j
        print(" titles:", json.dumps(fi.get("trTitleList"), ensure_ascii=False)[:600])
        for row in fi.get("rowList", []):
            if row.get("title") in ("당기순이익", "지배주주순이익", "EPS"):
                print(" row", row["title"], json.dumps(row.get("columns"), ensure_ascii=False)[:500])
        break
    except Exception as e:
        print("ERR", path, type(e).__name__, e)
