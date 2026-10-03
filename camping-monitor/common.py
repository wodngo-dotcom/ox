"""설정 로드 / URL 생성 / 브라우저 실행 등 discover.py, monitor.py 공용 코드."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 네이버 모바일 페이지를 그대로 받기 위해 모바일 기기로 위장
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


def load_config(path=None):
    path = Path(path or os.environ.get("CAMPING_CONFIG") or BASE_DIR / "config.json")
    if not path.exists():
        raise SystemExit(f"설정 파일이 없습니다: {path}\n  cp config.example.json config.json 후 수정하세요.")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_dir"] = str(path.resolve().parent)
    return cfg


def resolve(cfg, rel):
    p = Path(rel)
    return p if p.is_absolute() else Path(cfg["_dir"]) / p


def page_url(cfg):
    return cfg["page_url"].format(
        place_id=cfg["place_id"],
        checkin=cfg["checkin"],
        checkout=cfg["checkout"],
        guests=cfg["guests"],
    )


def launch_browser(p, headless=True):
    # CHROMIUM_PATH: 이미 설치된 크롬/크로미움을 쓰고 싶을 때만 지정
    kwargs = {"headless": headless}
    if os.environ.get("CHROMIUM_PATH"):
        kwargs["executable_path"] = os.environ["CHROMIUM_PATH"]
    return p.chromium.launch(**kwargs)


def new_context(browser):
    return browser.new_context(
        user_agent=MOBILE_UA,
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
    )


def scroll_to_bottom(page, rounds=6):
    """지연 로딩되는 객실/사이트 목록을 끝까지 불러오기 위해 스크롤."""
    for _ in range(rounds):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(600)
