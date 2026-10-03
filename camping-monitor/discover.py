"""1단계: 페이지를 열어 네트워크 요청을 기록하고, 예약 가능 여부 API 후보를 찾는다.

개발자도구 Network 탭을 자동으로 훑는 것과 같다.

  python discover.py                       # config.json 날짜로 분석
  python discover.py --checkin 20261104 --checkout 20261105 --out discovery_open
                                           # 빈자리가 있을 법한 평일로 한 번 더 → 두 결과 비교
  python discover.py --select 3            # 후보 #3 요청을 api 모드용으로 저장
"""
import argparse
import json
import re
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from common import launch_browser, load_config, new_context, page_url, resolve, scroll_to_bottom

# 예약 가능 여부 API에서 흔히 보이는 단어들 (URL, GraphQL operationName, 응답 본문)
KEYWORDS = [
    "graphql", "booking", "bizitem", "schedule", "stock", "remain", "available",
    "saleable", "isSale", "soldout", "room", "inventory", "calendar", "price",
]


def score(entry):
    hay = (entry["url"] + " " + (entry.get("post_data") or "") + " " + entry.get("body_preview", "")).lower()
    return sum(hay.count(k.lower()) for k in KEYWORDS)


def run(cfg, out_dir, checkin=None, checkout=None, headed=False):
    if checkin:
        cfg["checkin"] = checkin
    if checkout:
        cfg["checkout"] = checkout
    url = page_url(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("resp_*.json"):
        old.unlink()

    entries = []

    def on_response(resp):
        req = resp.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        try:
            body = resp.text()
        except Exception:
            return
        idx = len(entries)
        entry = {
            "idx": idx,
            "method": req.method,
            "url": req.url,
            "status": resp.status,
            "post_data": req.post_data,
            "headers": req.headers,
            "body_preview": body[:3000],
        }
        try:
            entry["json"] = json.loads(body)
        except Exception:
            pass
        entries.append(entry)
        (out_dir / f"resp_{idx:03d}.json").write_text(
            json.dumps({k: v for k, v in entry.items() if k != "body_preview"} | {"body": body},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    with sync_playwright() as p:
        browser = launch_browser(p, headless=not headed)
        ctx = new_context(browser)
        page = ctx.new_page()
        page.on("response", on_response)
        print(f"열기: {url}")
        page.goto(url, wait_until="networkidle", timeout=60000)
        scroll_to_bottom(page)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(out_dir / "page.png"), full_page=True)
        (out_dir / "page_text.txt").write_text(page.inner_text("body"), encoding="utf-8")
        (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
        browser.close()

    ranked = sorted(entries, key=score, reverse=True)
    summary = [
        {"idx": e["idx"], "score": score(e), "method": e["method"], "status": e["status"],
         "url": e["url"][:200], "post_data": (e["post_data"] or "")[:200]}
        for e in ranked
    ]
    (out_dir / "candidates.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nXHR/fetch 요청 {len(entries)}개 기록 → {out_dir}/")
    print("예약 가능 여부 API 후보 (점수 높은 순):")
    for s in summary[:15]:
        op = re.search(r'"operationName"\s*:\s*"([^"]+)"', s["post_data"])
        op = f" op={op.group(1)}" if op else ""
        print(f"  #{s['idx']:<3} score={s['score']:<3} {s['method']} {s['status']} {s['url'][:110]}{op}")
    print(f"\n화면 텍스트: {out_dir}/page_text.txt, 스크린샷: {out_dir}/page.png")
    print("다음 단계: 후보 resp_NNN.json 을 열어 '마감/가능'이 드러나는 필드를 찾고,")
    print("          python discover.py --select NNN 으로 api 모드용 요청을 저장하세요.")


def select(cfg, out_dir, idx):
    src = out_dir / f"resp_{idx:03d}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    dst = resolve(cfg, cfg["api"]["request_file"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: data[k] for k in ("method", "url", "post_data", "headers")}
    dst.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy(src, dst.with_name("selected_response_sample.json"))
    print(f"저장: {dst}\n이제 config.json 의 api.items_path / available_if 를 응답 구조에 맞게 채우고 mode 를 \"api\" 로 바꾸세요.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--checkin")
    ap.add_argument("--checkout")
    ap.add_argument("--out", default="discovery")
    ap.add_argument("--headed", action="store_true", help="브라우저 창을 띄워서 보기")
    ap.add_argument("--select", type=int, metavar="IDX", help="후보 요청을 api 모드용으로 저장")
    a = ap.parse_args()
    cfg = load_config(a.config)
    out = resolve(cfg, a.out)
    if a.select is not None:
        select(cfg, out, a.select)
    else:
        run(cfg, out, a.checkin, a.checkout, a.headed)
