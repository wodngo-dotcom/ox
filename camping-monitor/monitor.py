"""2단계: 주기적으로 빈자리를 확인하고, 새로 열린 자리가 있으면 ntfy.sh 로 푸시한다.

  python monitor.py --once          # 한 번만 확인하고 판별 결과 출력 (검증용)
  python monitor.py --test-notify   # 알림 테스트
  python monitor.py                 # 감시 시작 (5분 + 랜덤 지연 간격)
"""
import argparse
import json
import random
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

from common import launch_browser, load_config, new_context, page_url, resolve, scroll_to_bottom

ERROR_ALERT_AFTER = 6  # 연속 실패가 이만큼 쌓이면 (약 30분) "감시가 막혔다" 알림 1회


def log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- DOM 모드

# 객실/사이트 카드(li)마다 "마감" 표시 / 예약 링크·버튼 유무로 상태를 판별한다.
# 페이지 전체 텍스트에서 키워드를 찾는 방식과 달리, 카드 단위로 보므로
# "A구역 마감, B구역 예약 가능" 같은 혼재 상황도 구분된다.
DOM_JS = r"""
(cfg) => {
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const closedRe = new RegExp(cfg.closed.map(esc).join('|'));
  const availRe = new RegExp(cfg.avail.map(esc).join('|'));
  const priceRe = /[\d,]+\s*원/;
  const bookingLink = 'a[href*="booking.naver.com"], a[href*="/booking/"], a[href*="bizItem"]';
  const bodyText = document.body ? document.body.innerText : '';
  const pageClosed = cfg.pageClosed.some(k => bodyText.includes(k));

  const isCard = el => {
    const t = el.innerText || '';
    return !!el.querySelector(bookingLink) || closedRe.test(t) || (priceRe.test(t) && availRe.test(t));
  };
  let cards = [...document.querySelectorAll('li')].filter(isCard);
  cards = cards.filter(li => !cards.some(o => o !== li && li.contains(o)));  // 가장 안쪽 카드만

  const items = cards.map(li => {
    const t = (li.innerText || '').trim();
    const name = (t.split('\n').map(s => s.trim()).filter(Boolean)[0] || '').slice(0, 60);
    const closed = closedRe.test(t);
    const avail = !closed && (!!li.querySelector(bookingLink) || availRe.test(t));
    return { name, status: closed ? 'closed' : (avail ? 'available' : 'unknown'), text: t.slice(0, 200) };
  });
  return { pageClosed, items, title: document.title, textLen: bodyText.length };
}
"""


def check_dom(cfg, browser):
    url = page_url(cfg)
    d = cfg["dom"]
    ctx = new_context(browser)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
        scroll_to_bottom(page, rounds=4)
        page.wait_for_timeout(1500)
        r = page.evaluate(DOM_JS, {
            "closed": d["closed_keywords"],
            "avail": d["available_keywords"],
            "pageClosed": d["page_closed_keywords"],
        })
        items = r["items"]
        if not items and not r["pageClosed"]:
            save_debug(cfg, page, "no_items")
            return {"ok": False, "reason": f"객실 카드를 찾지 못함 (title={r['title']!r}, 텍스트 {r['textLen']}자)"}
        return {"ok": True, "items": items,
                "available": [i["name"] for i in items if i["status"] == "available"],
                "page_closed": r["pageClosed"]}
    finally:
        ctx.close()


def save_debug(cfg, page, tag):
    d = resolve(cfg, cfg.get("debug_dir", "debug"))
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(d / f"{stamp}_{tag}.png"), full_page=True)
        (d / f"{stamp}_{tag}.txt").write_text(page.inner_text("body"), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- API 모드

def get_path(obj, path):
    """'data.rooms[].items[]' 처럼 점 경로를 따라가며, [] 는 리스트를 펼친다."""
    cur = [obj]
    for seg in filter(None, path.split(".")):
        flat = seg.endswith("[]")
        key = seg[:-2] if flat else seg
        nxt = []
        for c in cur:
            v = c.get(key) if isinstance(c, dict) and key else c
            if v is None:
                continue
            if flat and isinstance(v, list):
                nxt.extend(v)
            else:
                nxt.append(v)
        cur = nxt
    return cur


OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    ">": lambda a, b: a is not None and a > b, ">=": lambda a, b: a is not None and a >= b,
    "<": lambda a, b: a is not None and a < b, "<=": lambda a, b: a is not None and a <= b,
    "truthy": lambda a, b: bool(a),
}


def check_api(cfg, _browser=None):
    a = cfg["api"]
    req = json.loads(resolve(cfg, a["request_file"]).read_text(encoding="utf-8"))
    headers = {k: v for k, v in req["headers"].items()
               if not k.startswith(":") and k.lower() not in ("content-length", "host", "accept-encoding")}
    resp = requests.request(req["method"], req["url"], headers=headers,
                            data=(req.get("post_data") or "").encode("utf-8") or None, timeout=30)
    if resp.status_code != 200:
        return {"ok": False, "reason": f"API HTTP {resp.status_code}"}
    data = resp.json()
    items = get_path(data, a["items_path"])
    if not items:
        return {"ok": False, "reason": f"items_path '{a['items_path']}' 에서 항목을 찾지 못함 (응답 구조 변경?)"}
    cond = a["available_if"]
    out = []
    for i, it in enumerate(items):
        val = get_path(it, cond["field"])
        val = val[0] if val else None
        name = str((get_path(it, a.get("name_field", "name")) or [f"item{i}"])[0])
        ok = OPS[cond["op"]](val, cond.get("value"))
        out.append({"name": name, "status": "available" if ok else "closed", "text": f"{cond['field']}={val!r}"})
    return {"ok": True, "items": out, "available": [o["name"] for o in out if o["status"] == "available"]}


# ---------------------------------------------------------------- 알림 / 상태

def notify(cfg, title, message, priority=5, tags=("tent",)):
    url = page_url(cfg)
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": title,
        "message": message,
        "priority": priority,
        "tags": list(tags),
        "click": url,  # 알림을 누르면 예약 페이지로 이동
        "actions": [{"action": "view", "label": "예약 페이지 열기", "url": url}],
    }
    r = requests.post(cfg["ntfy_server"].rstrip("/"), json=payload, timeout=15)
    r.raise_for_status()


def fmt_dates(cfg):
    ci, co = cfg["checkin"], cfg["checkout"]
    return f"{int(ci[4:6])}/{int(ci[6:])}~{int(co[4:6])}/{int(co[6:])} {cfg['guests']}명"


def load_state(cfg):
    p = resolve(cfg, cfg.get("state_file", "state.json"))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"available": []}


def save_state(cfg, state):
    p = resolve(cfg, cfg.get("state_file", "state.json"))
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def unique_names(names):
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} #{seen[n]}")
    return out


def process(cfg, result, state):
    """판별 결과를 상태와 비교. 새로 열린 자리가 있을 때만 알림. 반환: 보낸 알림 수."""
    now = unique_names(result["available"])
    prev = set(state.get("available", []))
    new = [n for n in now if n not in prev]
    sent = 0
    if new:
        msg = "새로 열림: " + ", ".join(new)
        if len(now) > len(new):
            msg += "\n전체 가능: " + ", ".join(now)
        notify(cfg, f"⛺ 빈자리! {fmt_dates(cfg)}", msg)
        sent = 1
    # 마감된 자리는 목록에서 빠지므로, 다시 풀리면 'new' 로 잡혀 재알림된다
    state["available"] = now
    state["checked_at"] = datetime.now().isoformat(timespec="seconds")
    return sent


def run_check(cfg, browser):
    fn = check_api if cfg.get("mode") == "api" else check_dom
    try:
        return fn(cfg, browser)
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def print_result(r):
    if not r["ok"]:
        log(f"판별 실패: {r['reason']}")
        return
    if r.get("page_closed"):
        log("페이지에 '예약 가능한 객실 없음' 문구 감지")
    for it in r["items"]:
        mark = {"available": "🟢 가능", "closed": "🔴 마감"}.get(it["status"], "⚪ 불명")
        log(f"  {mark}  {it['name']}   | {it['text'][:80]!r}")
    log(f"예약 가능: {len(r['available'])}개 / 항목 {len(r['items'])}개")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--once", action="store_true", help="한 번 확인하고 결과만 출력 (알림/상태 변경 없음)")
    ap.add_argument("--test-notify", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)

    if a.test_notify:
        notify(cfg, f"테스트 알림 ({fmt_dates(cfg)})", "이 알림을 누르면 예약 페이지가 열려야 합니다.", priority=3)
        log("테스트 알림 전송 완료")
        return

    with sync_playwright() as p:
        browser = launch_browser(p) if cfg.get("mode") != "api" else None
        if a.once:
            r = run_check(cfg, browser)
            print_result(r)
            sys.exit(0 if r["ok"] else 1)

        log(f"감시 시작: {fmt_dates(cfg)}  mode={cfg.get('mode', 'dom')}  topic={cfg['ntfy_topic']}")
        fails = 0
        while True:
            r = run_check(cfg, browser)
            print_result(r)
            if r["ok"]:
                fails = 0
                state = load_state(cfg)
                try:
                    if process(cfg, r, state):
                        log("알림 전송!")
                    save_state(cfg, state)
                except requests.RequestException as e:
                    log(f"알림 전송 실패 (다음 회차에 재시도): {e}")  # 상태 저장 안 함 → 재시도
            else:
                fails += 1
                if fails == ERROR_ALERT_AFTER:
                    try:
                        notify(cfg, "캠핑 감시 오류", f"{fails}회 연속 확인 실패: {r['reason']}", priority=3, tags=("warning",))
                    except requests.RequestException:
                        pass
                if browser is not None and fails % 3 == 0:  # 브라우저가 망가졌을 수 있으니 재시작
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = launch_browser(p)
            wait = cfg["interval_sec"] + random.uniform(0, cfg.get("jitter_sec", 0))
            log(f"다음 확인까지 {wait / 60:.1f}분")
            time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("종료")
