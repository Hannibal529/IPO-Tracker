# IPO 트래커

DART OpenAPI와 38커뮤니케이션 데이터로 신규 상장 종목을 자동으로 정리하는 팀 내부용 사이트입니다.
GitHub Actions가 평일 하루 2회(08:17, 17:47 KST) 수집하고, 결과를 GitHub Pages에 올립니다. 서버와 비용은 필요 없습니다.

## 화면
| 탭 | 내용 | 출처 |
|---|---|---|
| 공모 예정 | 일정, 밴드, 주관사 지표·평가가액·할인율, **재산정 적정가**, 경쟁률, 확약비율. 행을 누르면 상세 정보와 peer 표 | DART 증권신고서(주요정보 API + 원문), 38 |
| IPO 캘린더 | 수요예측, 청약, 납입, 상장, 확약해제를 월간 달력으로 표시 | 위 데이터에서 계산 |
| 수요예측 추이 | 분기별 기관 경쟁률과 운용사 확약비율, 종목별 참여 내역 | 정정신고서·투자설명서·발행실적보고서 원문, 38 |
| 보호예수 해제 | 15일, 1·3·6개월 확약 해제일과 물량 | 기관 배정 확약 + 유통가능물량 표 |

해제일 계산은 기존 엑셀 수식과 같습니다. 15일 확약은 `WORKDAY(상장일+14,1)`, N개월 확약은 `WORKDAY(EDATE(상장일,N)-1,1)`로 계산하고, 한국 공휴일을 반영합니다.

## 적정가 재산정 로직 (`collector/valuation.py`)
1. 증권신고서 원문에서 주관사의 최종 유사기업, 적용 지표(PER/PSR/PBR/EV/EBITDA), 적용 멀티플, 주당 평가가액을 추출합니다.
2. 각 peer의 **현재 시가총액**과 **DART LTM 실적**으로 멀티플을 다시 계산합니다.
3. peer가 적합한지 판단합니다.
   - `ANTHROPIC_API_KEY`가 있으면 Claude가 사업내용을 비교해 맞지 않는 peer를 빼고, 같은 업종 상장사 중 적합한 기업을 더합니다. 적용 지표도 필요할 때 바꿉니다.
   - 키가 없으면 규칙 기반으로 처리합니다. 적자 기업은 제외하고, 중앙값의 2.5배를 넘거나 0.4배에 못 미치는 기업도 제외합니다.
4. 적정가는 `재산정 멀티플 중앙값 × (주관사 평가가액 ÷ 주관사 적용 멀티플)`로 구합니다. 괄호 안은 주관사가 쓴 주당 기준이익입니다.
   - 주관사와 다른 지표를 쓰려면 그 지표의 주당 기준값을 `data/manual.json`의 `target_basis`에 넣어야 합니다. 이 값이 없으면 주관사 지표를 그대로 씁니다.

## 설치 (최초 1회, 약 15분)
1. **DART API 키 발급**: https://opendart.fss.or.kr 에서 인증키를 신청합니다(무료, 즉시 발급).
2. **GitHub 저장소 생성**: 이 폴더 전체를 새 저장소에 올립니다.
   - 사이트 주소를 아는 사람만 볼 수 있게 하려면 저장소는 Private으로 만듭니다. 다만 Private 저장소에서 Pages를 쓰려면 GitHub Pro 이상이 필요합니다. 무료 계정이라면 Public 저장소를 쓰게 되는데, 이 경우 코드와 수집 데이터가 공개됩니다.
   - 어느 쪽이든 사이트에는 `noindex`와 `robots.txt`가 들어 있어서 검색엔진에는 노출되지 않습니다. 로그인은 없으므로, 링크를 받은 사람은 누구나 볼 수 있습니다.
3. **Secrets 등록**: Settings → Secrets and variables → Actions 에서 등록합니다.
   - `DART_API_KEY` (필수)
   - `ANTHROPIC_API_KEY` (선택. 있으면 Claude가 peer를 검토합니다. 종목당 호출 1회)
4. **Pages 설정**: Settings → Pages → Source를 **GitHub Actions**로 바꿉니다.
5. **첫 실행**: Actions 탭 → "IPO 데이터 수집·배포" → Run workflow. 끝나면 Pages 주소가 표시됩니다.

## 수동 보정 (`data/manual.json`)
자동 추출이 틀렸거나 비어 있을 때 종목명을 키로 값을 덮어씁니다. 파일을 수정해서 push하면 바로 다시 빌드됩니다.
```json
{
  "멜콘": {
    "listing": "2026-10-13",
    "special": false,
    "valuation": {
      "drop_peers": ["○○전자"],
      "add_peers": ["△△테크"],
      "method": "PER",
      "target_basis": {"PER": 540}
    }
  }
}
```

## 로컬 실행
```bash
pip install -r requirements.txt
export DART_API_KEY=...
python -m collector.excel_seed "IPO_2026-09-10.xlsx"   # 엑셀 → data/seed.json (최초 1회)
python -m collector.build                               # 수집 → site/data/ipo.json
cd site && python -m http.server 8000                   # http://localhost:8000
```

## 유의사항
- **38커뮤니케이션**: 공식 API가 없어서 HTML 표를 읽어 옵니다. 사이트 구조가 바뀌면 `collector/ipo38.py`만 수정하면 됩니다. 호출은 하루 2회로 제한했습니다. 팀 내부용 정도로만 쓰고, 외부 공개 서비스로 바꿀 때는 이용약관을 먼저 확인하세요.
- **원문 파싱**은 주관사마다 표 양식이 달라서 휴리스틱으로 동작합니다. 수집 중 실패한 항목은 사이트 상단에 경고로 표시되고, 비어 있는 값은 `manual.json`으로 보정합니다.
- 엑셀에서 옮긴 과거 데이터는 `data/seed.json`에 있습니다. 첫 수집 이후에는 `data/state.json`이 누적 저장소 역할을 합니다.
