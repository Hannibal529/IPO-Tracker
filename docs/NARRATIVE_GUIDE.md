# 보고서 서술 작성 가이드 (data/narratives.json)

`collector/report.py`가 종목별 PDF를 만들 때 숫자(공모개요·일정·밸류·재무)는 자동으로 채우고,
판단이 들어가는 문장은 `data/narratives.json`에서 읽는다. 이 파일은 Claude가 증권신고서를 직접 읽고 쓴다.

## 키와 필드

최상위 키는 DART `corp_code`(8자리 문자열). 값의 필드:

| 필드 | 형식 | 내용 |
|---|---|---|
| name | 문자열 | 종목명 |
| rcept_no | 문자열 | 서술 작성에 쓴 최신 신고서 접수번호 (state.json의 rcept_no와 같게) |
| updated | YYYY-MM-DD | 작성일 |
| grades | {"산업매력도"\|"성장성"\|"재무"\|"수급": [등급, 근거]} | 등급은 높음/보통/낮음, 양호/보통/취약, 부담 없음/다소 부담/부담 중 하나. 수급은 생략 가능(자동) |
| allocation | 문자열 | 예: "기관 70~75%, 일반 25~30% (우리사주 미배정)" |
| use_of_funds | 문자열 | 억원 단위 요약 (생략 시 자동) |
| investment_points | [문장] | 2~4개 |
| risks | [문장] | 3~4개 |
| overview | [문장] | 1~2개. 설립연도·소재지·사업·상장 방식 |
| business_table | {title, head, rows} | 선택. 파이프라인·사업부 표 |
| est_reliability | [문장] | 추정이익을 쓴 경우 달성 가능성(최근 실적 대비 진도율 등). LTM이면 적용 이익 설명 |
| supply_notes | [문장] | 유통물량·오버행·기존주주 취득단가 |
| peer_view | 문장 | 주관사 비교기업의 적정성, 재산정에서 빠진 기업(해외·적자·100배 초과) |
| discount_reason | 문장 | 추정이익 현가 할인율 근거. 바이오는 35%(민감도 25/45%), 비바이오는 주관사 할인율 |
| recent_ipo_note | 문장 | 선택. 최근 동종 IPO 흐름 |

## 문체

- 아델 항목이 기준 예시. 한 줄 문장, "~임/~함"으로 끝냄.
- 신고서에 있는 사실과 숫자만 씀. 숫자는 억원·%로 반올림하고 연도를 붙임.
- 참여·불참 같은 투자 의견은 쓰지 않음.
- 신고서 문구를 길게 옮기지 말고 요약함.
- 확정공모가·수요예측 결과가 나오면(정정신고서) overview나 risks에 반영함.

## 읽을 곳

신고서에서 다음을 확인함: 사업의 개요, 핵심투자위험(사업·회사·기타), 공모가 산정 요약표와 비교기업 선정,
추정 손익과 근거, 주주별 취득단가·의무보유, 유통가능물량, 그룹별 배정, 자금 사용 목적, 요약 재무.

## 작업 순서 (예약 작업)

1. `python -m collector.nar_todo` → 서술이 없거나(new) 신고서가 바뀐(changed) 종목 목록.
2. 목록 종목명을 쉼표로 이어 워크플로를 `brief`와 `dump` 입력에 같이 넣어 실행함
   (REST: `POST repos/Hannibal529/IPO-Tracker/actions/workflows/update.yml/dispatches`).
   끝나면 `debug-docs` 브랜치의 `debug/briefs/<종목>.txt`(요약본)와 `debug/<종목>.xml.gz`(원문)를 읽음.
3. changed 종목은 바뀐 부분(확정가, 수요예측 결과, 정정된 숫자)만 고치고, new 종목은 전부 씀.
4. `data/narratives.json`에 병합(rcept_no·updated 갱신)하고 commit/push → 워크플로가 PDF를 다시 만듦.
