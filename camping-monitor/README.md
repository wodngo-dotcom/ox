# 네이버 캠핑장 빈자리 알림

네이버 플레이스 캠핑장 예약 페이지를 주기적으로 확인해, 원하는 날짜에 빈자리가 나면
[ntfy](https://ntfy.sh) 푸시 알림을 휴대폰으로 보냅니다. 알림을 누르면 예약 페이지가 열립니다.

## 설치 (도구별로 왜 필요한지)

| 도구 | 필요한 이유 |
|---|---|
| Python 3.9+ | 스크립트 실행 |
| `playwright` | 네이버 플레이스는 자바스크립트로 객실 목록을 그리므로 실제 브라우저가 필요. 네트워크 요청(API) 분석에도 사용 |
| Chromium (`playwright install chromium`) | Playwright가 조종할 헤드리스 브라우저 |
| `requests` | ntfy 알림 전송, API 모드에서 예약 API 직접 호출 |
| ntfy 앱 (휴대폰) | 푸시 알림 수신. 가입 없이 토픽 이름만 구독하면 됨 |

```bash
cd camping-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json
```

`config.json`에서 날짜·인원·URL·`ntfy_topic`을 수정하세요. 토픽 이름은 누구나 구독할 수 있으니
추측하기 어려운 이름(예: `camping-1030-k8f2x9`)으로 정하고, 휴대폰 ntfy 앱에서 같은 토픽을 구독합니다.

| 설정 | 설명 |
|---|---|
| `checkin` / `checkout` / `guests` | 날짜(YYYYMMDD), 인원 |
| `page_url` | `{place_id}` `{checkin}` `{checkout}` `{guests}` 자리표시자 사용 |
| `ntfy_server` / `ntfy_topic` | 알림 서버/토픽 |
| `interval_sec` / `jitter_sec` | 기본 300초 + 0~120초 랜덤 지연 (네이버 차단 방지) |
| `mode` | `dom`(화면 판별, 기본) 또는 `api`(예약 API 직접 호출) |

## 1단계: 네트워크 분석 (API 찾기)

```bash
python discover.py                                   # 설정 날짜(마감 상태)로 기록
python discover.py --checkin 20261104 --checkout 20261105 --out discovery_open   # 빈자리 있는 날짜로 비교
```

페이지의 XHR/fetch 요청을 전부 `discovery/resp_NNN.json`에 저장하고, 예약 관련 키워드
(graphql, booking, bizItem, stock, available …)가 많은 순으로 후보를 출력합니다.
마감일·가능일 두 결과에서 **값이 달라지는 필드**(예: `isAvailable`, `remainStock`)가 판별 기준입니다.

API를 찾았다면:

```bash
python discover.py --select 12      # 후보 #12 요청을 discovery/selected_request.json 으로 저장
```

`config.json`의 `api`를 응답 구조에 맞게 채우고 `"mode": "api"`로 바꿉니다.

```json
"api": {
  "request_file": "discovery/selected_request.json",
  "items_path": "data.rooms[]",
  "name_field": "name",
  "available_if": { "field": "remainStock", "op": ">", "value": 0 }
}
```

`items_path`는 점 경로이고 `[]`는 리스트를 펼칩니다. `op`는 `== != > >= < <= truthy`.
API 모드는 브라우저 없이 요청 한 번이라 가볍지만, 저장된 요청에 날짜가 박혀 있으므로
**날짜를 바꾸면 discover를 다시 실행**해야 합니다. 쿠키/토큰이 만료돼 실패하면 역시 다시 실행하세요.

API를 못 찾았거나 번거로우면 기본 `dom` 모드를 그대로 쓰면 됩니다.

## 2단계: 판별이 맞는지 확인

```bash
python monitor.py --once           # 객실별 🟢 가능 / 🔴 마감 판별 결과 출력 (알림 안 보냄)
python monitor.py --test-notify    # 휴대폰에 테스트 알림 → 눌러서 예약 페이지가 열리는지 확인
```

마감인 날짜와 빈자리가 있는 날짜 각각으로 `--once`를 돌려 결과가 실제 화면과 같은지 확인하세요.
DOM 모드는 페이지 전체 텍스트가 아니라 **객실 카드(li) 단위**로 "마감" 표시와 예약 링크 유무를 봅니다.
판별이 틀리면 `discovery/page_text.txt`를 보고 `dom.closed_keywords` / `available_keywords`를 조정하세요.
카드를 하나도 못 찾으면(차단·오류 페이지) "마감"이 아니라 **판별 실패**로 처리하고 `debug/`에 스크린샷을 남깁니다.

## 3단계: 감시 시작

```bash
python monitor.py
# 백그라운드: nohup python monitor.py > monitor.log 2>&1 &
```

- 새로 열린 자리가 있을 때만 알림 → 같은 빈자리로 반복 알림 없음 (`state.json`에 기억)
- 그 자리가 다시 마감되면 목록에서 빠지고, 다시 풀리면 재알림
- 확인이 6회 연속 실패하면(약 30분) "감시 오류" 알림 1회
- 알림 전송 실패 시 상태를 저장하지 않아 다음 회차에 재전송

## 테스트

```bash
python -m unittest discover -s tests -v
```

모의 페이지(전부 마감 / 일부 가능 / 페이지 단위 마감 문구 / 오류 페이지)와 가짜 ntfy 서버로
판별과 "마감→열림→유지→추가 열림→마감→재열림" 알림 순서를 검증합니다.
