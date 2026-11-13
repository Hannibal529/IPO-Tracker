"""네이버 기업실적분석 표 구조 점검용: python -m collector.debug_naver 000100"""
import io
import sys

import pandas as pd
import requests

code = sys.argv[1] if len(sys.argv) > 1 else "000100"
r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
print("status", r.status_code, "len", len(r.content), "enc", r.encoding)
r.encoding = r.encoding if (r.encoding or "").lower() not in ("iso-8859-1", "") else r.apparent_encoding
i = r.text.find("기업실적분석")
print("기업실적분석 at", i)
for k, df in enumerate(pd.read_html(io.StringIO(r.text))):
    cols = [" | ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
    if any("(E)" in c for c in cols) or k < 2:
        print("== table", k, df.shape)
        print("cols:", cols[:14])
        print(df.iloc[:16, :3].to_string()[:1500])
