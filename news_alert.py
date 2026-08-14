# -*- coding: utf-8 -*-
"""
토스증권 뉴스 알리미
- 토스증권(tossinvest.com)이 모아주는 종목별 뉴스를 주기적으로 확인
- 새 뉴스가 뜨면 Claude AI가 호재/악재/중립 + 중요도를 판단
- 조건을 만족하면 텔레그램으로 알림 전송

사용법:
  python news_alert.py              # 무한 루프로 실행 (config.json의 확인주기마다 반복)
  python news_alert.py --once       # 한 번만 확인하고 종료
  python news_alert.py --daily      # 미국 장 마감 후 1회: 뉴스분석 + 아침 브리핑 + 실적 알림 (작업 스케줄러용)
  python news_alert.py --price-watch  # 급등락 감시 1회 (작업 스케줄러가 30분마다 실행)
  python news_alert.py --weekly     # 주간 성적표 전송 (작업 스케줄러가 일요일마다 실행)
  python news_alert.py --commands   # 텔레그램으로 받은 명령(/새로고침, /토론 NVDA 등) 처리
  python news_alert.py --test       # 텔레그램으로 테스트 메시지 전송
  python news_alert.py --get-chat-id  # 봇에게 메시지를 보낸 뒤 실행하면 채팅 ID를 알려줌
  (--daily/--weekly에 --force를 붙이면 중복 가드를 무시하고 강제 실행)
"""
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH = os.path.join(BASE_DIR, "news_log.txt")
FEED_PATH = os.path.join(BASE_DIR, "feed.json")
FEED_JS_PATH = os.path.join(BASE_DIR, "feed.js")
TEAM_PATH = os.path.join(BASE_DIR, "team.json")
DEBATE_PATH = os.path.join(BASE_DIR, "debates.json")
STOCKS_PATH = os.path.join(BASE_DIR, "stocks.json")

TOSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}

VERDICT_EMOJI = {"호재": "🟢", "악재": "🔴", "중립": "⚪"}


def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


MAX_STOCKS = 30      # 종목이 늘수록 실행 시간과 API 비용이 비례해 늘어난다


def load_stocks():
    """감시 종목 목록 (내 PC와 클라우드가 공유하는 단일 목록)"""
    if os.path.exists(STOCKS_PATH):
        try:
            with open(STOCKS_PATH, encoding="utf-8") as f:
                return [str(t) for t in (json.load(f).get("종목") or [])]
        except (json.JSONDecodeError, OSError, AttributeError):
            log("stocks.json 을 읽지 못했습니다 — config 의 목록을 씁니다")
    return None


def save_stocks(tickers):
    with open(STOCKS_PATH, "w", encoding="utf-8") as f:
        json.dump({"종목": tickers}, f, ensure_ascii=False, indent=1)


def load_config():
    """config.json을 읽고, 환경변수가 있으면 그 값으로 덮어쓴다.
    (GitHub Actions 등 클라우드 실행용 — 비밀키를 파일에 두지 않기 위함)"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    elif os.path.exists(CONFIG_PATH + ".example"):
        with open(CONFIG_PATH + ".example", encoding="utf-8") as f:
            cfg = json.load(f)

    env_map = {
        "TELEGRAM_TOKEN": "텔레그램_토큰",
        "TELEGRAM_CHAT_ID": "텔레그램_채팅ID",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    }
    for env_key, cfg_key in env_map.items():
        v = os.environ.get(env_key)
        if v:
            cfg[cfg_key] = v
    # 감시 종목은 stocks.json 하나로 관리한다.
    # config.json 은 gitignore 라 클라우드에 없고, config.json.example 은
    # 저장소에 있어서 예전에는 종목을 두 군데에 똑같이 적어야 했다.
    # stocks.json 은 저장소에 올라가므로 내 PC와 클라우드가 같은 목록을 본다.
    stocks = load_stocks()
    if stocks:
        cfg["종목"] = stocks
    # 환경변수가 있으면 그게 최우선 (임시로 다른 목록을 쓰고 싶을 때)
    if os.environ.get("STOCKS"):
        cfg["종목"] = [s.strip() for s in os.environ["STOCKS"].split(",") if s.strip()]

    if not cfg:
        log(f"config.json이 없습니다: {CONFIG_PATH}")
        sys.exit(1)
    return cfg


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen": {}, "codes": {}, "names": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def load_feed():
    if os.path.exists(FEED_PATH):
        try:
            with open(FEED_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def load_team():
    if os.path.exists(TEAM_PATH):
        try:
            with open(TEAM_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def load_debates():
    if os.path.exists(DEBATE_PATH):
        try:
            with open(DEBATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def load_feed_stocks():
    """feed.js 에 저장돼 있는 시세 목록을 그대로 가져온다.
    토론만 다시 저장할 때 시세를 날리지 않기 위함."""
    if os.path.exists(FEED_JS_PATH):
        try:
            with open(FEED_JS_PATH, encoding="utf-8") as f:
                m = re.search(r"window\.FEED_META = (.*?);\n", f.read())
            if m:
                return json.loads(m.group(1)).get("stocks", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_feed(feed, stocks, team=None, debates=None, macro=None):
    """대시보드(index.html)가 읽는 feed.json + team.json + feed.js 저장"""
    feed = feed[:300]
    if team is None:  # 시세만 갱신할 때는 기존 진단 유지
        team = load_team()
    if debates is None:
        debates = load_debates()
    with open(DEBATE_PATH, "w", encoding="utf-8") as f:
        json.dump(debates, f, ensure_ascii=False, indent=1)
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    with open(TEAM_PATH, "w", encoding="utf-8") as f:
        json.dump(team, f, ensure_ascii=False, indent=1)
    meta = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stocks": stocks,
        "macro": macro if macro is not None else load_state().get("macro", []),
    }
    with open(FEED_JS_PATH, "w", encoding="utf-8") as f:
        f.write("window.FEED_META = " + json.dumps(meta, ensure_ascii=False) + ";\n")
        f.write("window.NEWS_FEED = " + json.dumps(feed, ensure_ascii=False) + ";\n")
        f.write("window.TEAM = " + json.dumps(team, ensure_ascii=False) + ";\n")
        f.write("window.DEBATES = " + json.dumps(debates, ensure_ascii=False) + ";\n")


def refresh_prices(config, state):
    """시세만 빠르게 갱신해서 대시보드 데이터(feed.js) 업데이트"""
    tickers = [str(t) for t in config.get("종목", []) if str(t) in state["codes"]]
    if not tickers:
        log("등록된 종목이 없습니다. 뉴스 확인을 먼저 실행하세요.")
        return None
    prices = fetch_prices([state["codes"][t]["page"] for t in tickers])
    stocks = []
    for t in tickers:
        close, chg = prices.get(state["codes"][t]["page"], (None, None))
        stocks.append({"ticker": t, "name": state["names"].get(t, t),
                       "price": close, "change": chg})
    macro = fetch_macro()          # 풍향계도 같이 갱신 (6번 호출, 1초 남짓)
    if macro:
        state["macro"] = macro
        save_state(state)
    save_feed(load_feed(), stocks, macro=macro or None)
    log(f"시세 갱신 완료 — {len(stocks)}종목, 풍향계 {len(macro)}개")
    return prices


# ---------------- 토스증권 API ----------------

def lookup_stock(ticker):
    """티커/종목코드 → (뉴스조회용 companyCode, 종목명, 종목페이지 code, ISIN)"""
    url = f"https://wts-info-api.tossinvest.com/api/v2/stock-infos/code-or-symbol/{ticker}"
    r = requests.get(url, headers=TOSS_HEADERS, timeout=15)
    r.raise_for_status()
    d = r.json()["result"]
    return d["companyCode"], d["name"], d["code"], d.get("isinCode")


def get_isin(state, ticker):
    """커뮤니티 조회용 ISIN. 예전 state에는 없을 수 있어 지연 보충."""
    info = state["codes"].get(ticker, {})
    if not info.get("isin"):
        try:
            _, _, _, isin = lookup_stock(ticker)
            info["isin"] = isin
        except (requests.RequestException, KeyError):
            return None
    return info.get("isin")


def fetch_news(company_code, size=20):
    url = (f"https://wts-info-api.tossinvest.com/api/v2/news/companies/"
           f"{company_code}?size={size}&orderBy=latest")
    r = requests.get(url, headers=TOSS_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["result"]["body"]


NAVER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_candles(page_code, count=260):
    """토스 일봉 캔들 → (날짜, 종가, 고가, 저가, 환율). 과거→최신 순
    환율: 미국 종목은 원/달러, 한국 종목은 1"""
    market = "kr-s" if page_code.startswith("A") else "us-s"
    url = (f"https://wts-info-api.tossinvest.com/api/v1/c-chart/{market}/"
           f"{page_code}/day:1?count={count}&useAdjustedRate=true")
    r = requests.get(url, headers=TOSS_HEADERS, timeout=15)
    r.raise_for_status()
    res = r.json()["result"]
    candles = list(reversed(res["candles"]))  # 원본은 최신이 먼저
    return ([c["dt"][:10] for c in candles], [c["close"] for c in candles],
            [c["high"] for c in candles], [c["low"] for c in candles],
            float(res.get("exchangeRate") or 1))


def compute_atr(closes, highs, lows, period=14):
    """ATR(평균 실제 범위) — 변동성 기반 손절폭 계산용"""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(len(closes) - period, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return round(sum(trs) / len(trs), 4)


def position_size(config, price, atr):
    """ATR 기반 적정 수량 — '한 번에 잔고의 N%만 잃는다' 원칙
    주문수량 = (계좌 × 위험%) ÷ (ATR × 손절배수)
    ※ 수량을 먼저 정하고 손절을 끼워 맞추면 한도가 깨지므로 손절폭이 수량을 정한다"""
    equity = float(config.get("계좌금액", 0) or 0)
    if not equity or not price or not atr or atr <= 0:
        return None
    risk_pct = float(config.get("거래당_위험퍼센트", 1)) / 100
    mult = float(config.get("손절_ATR배수", 2))
    stop_dist = atr * mult
    qty = math.floor(equity * risk_pct / stop_dist)
    if qty < 1:
        return {"qty": 0, "note": "1주도 위험한도를 넘음 — 계좌 대비 변동성 과대"}
    invest = qty * price
    capped = False
    max_w = float(config.get("종목당_최대비중", 20)) / 100
    if invest > equity * max_w:          # 비중 상한으로 한 번 더 조임
        qty = math.floor(equity * max_w / price)
        invest, capped = qty * price, True
    return {
        "qty": qty,
        "stop": round(price - stop_dist, 2),
        "stop_dist": round(stop_dist, 2),
        "invest": round(invest),
        "weight": round(invest / equity * 100, 1),
        "risk": round(qty * stop_dist),
        "capped": capped,
    }


def fetch_community_heat(state, ticker):
    """토스 커뮤니티 댓글 속도(시간당 개수). 최근 2페이지(약 22개)의 시간 간격으로 계산.
    subjectId: 한국 종목=ISIN, 미국 종목=페이지 코드"""
    page = state["codes"].get(ticker, {}).get("page", "")
    isin = get_isin(state, ticker) if page.startswith("A") else page
    if not isin:
        return None
    from datetime import timezone
    now = datetime.now(timezone.utc)
    times, key = [], None
    for _ in range(2):
        url = ("https://wts-cert-api.tossinvest.com/api/v4/comments"
               f"?subjectType=STOCK&subjectId={isin}&commentSortType=RECENT")
        if key:
            url += f"&key={key}"
        d = requests.get(url, headers=TOSS_HEADERS, timeout=15).json()["result"]
        for c in d.get("results", []):
            try:
                times.append(datetime.fromisoformat(
                    re.sub(r"\.(\d{1,6})\d*", r".\1", c["createdAt"])))
            except (ValueError, KeyError, TypeError):
                continue
        if not d.get("hasNext"):
            break
        key = d.get("key")
    if not times:
        return 0.0
    hours = max((now - min(times)).total_seconds() / 3600, 0.1)
    if hours > 24 and len(times) < 5:  # 며칠에 한두 개 수준이면 사실상 0
        return 0.0
    return round(len(times) / hours, 1)


def fetch_hot_ranks():
    """토스 실시간 인기 순위 → {페이지코드: 순위}"""
    url = "https://wts-info-api.tossinvest.com/api/v1/rankings/realtime/stock?size=100"
    r = requests.get(url, headers=TOSS_HEADERS, timeout=15)
    r.raise_for_status()
    return {d["code"]: i + 1 for i, d in enumerate(r.json()["result"]["data"])}


def vibe_verdict(rate):
    """시간당 댓글 수 → 민심 판정"""
    if rate is None:
        return "-"
    if rate >= 30:
        return "과열"
    if rate >= 10:
        return "시끌"
    if rate >= 3:
        return "보통"
    return "조용"


def fetch_earnings(state, config):
    """나스닥 캘린더에서 향후 7일 실적 발표일 → state['earnings'] 갱신 (하루 1회)"""
    from zoneinfo import ZoneInfo
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    cache = state.setdefault("earnings", {})
    if cache.get("_fetched") == today_ny.isoformat():
        return cache
    symbols = {str(t).upper() for t in config.get("종목", []) if not str(t).isdigit()}
    found = {}
    hdr = {"User-Agent": NAVER_HEADERS["User-Agent"], "Accept": "application/json"}
    for i in range(8):
        day = today_ny + timedelta(days=i)
        try:
            r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={day}",
                             headers=hdr, timeout=15)
            rows = ((r.json().get("data") or {}).get("rows")) or []
            for row in rows:
                sym = (row.get("symbol") or "").upper()
                if sym in symbols:
                    found[sym] = day.isoformat()
        except (requests.RequestException, ValueError):
            continue
    state["earnings"] = {"_fetched": today_ny.isoformat(), **found}
    return state["earnings"]


def grade_feed(feed, candle_map):
    """호재/악재 판정을 이후 실제 주가로 채점. candle_map: {ticker:(dates,closes)}
    장중이라 아직 안 끝난 오늘 캔들은 채점에 쓰지 않는다."""
    from zoneinfo import ZoneInfo
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    ny_today = ny_now.date().isoformat()
    # 뉴욕 16시(장 마감) 전이면 오늘 캔들 제외, 마감 후면 오늘 캔들까지 사용
    market_closed = ny_now.hour >= 16
    graded = []
    for n in feed:
        if n.get("verdict") not in ("호재", "악재") or "hit5" in n:
            continue
        cm = candle_map.get(n.get("ticker"))
        if not cm:
            continue
        dates, closes = cm
        while dates and (dates[-1] > ny_today or (dates[-1] == ny_today and not market_closed)):
            dates, closes = dates[:-1], closes[:-1]
        news_date = (n.get("createdAt") or "")[:10]
        if not news_date:
            continue
        base = None
        for i in range(len(dates) - 1, -1, -1):
            if dates[i] <= news_date:
                base = i
                break
        if base is None:
            continue
        direction = 1 if n["verdict"] == "호재" else -1
        if "hit1" not in n and base + 1 < len(dates):
            chg = round((closes[base + 1] / closes[base] - 1) * 100, 2)
            n["chk1"] = chg
            n["hit1"] = (chg * direction) > 0
            graded.append(n)
        if base + 5 < len(dates):
            chg = round((closes[base + 5] / closes[base] - 1) * 100, 2)
            n["chk5"] = chg
            n["hit5"] = (chg * direction) > 0
    return graded  # 이번에 새로 채점된 뉴스 목록


def compute_tech(closes):
    """종가 리스트(과거→최신) → 기술적 지표 dict"""
    if len(closes) < 21:
        return None
    price = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    # RSI(14)
    gains, losses = [], []
    for a, b in zip(closes[-15:-1], closes[-14:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_loss = sum(losses) / 14
    rsi = 100.0 if avg_loss == 0 else round(100 - 100 / (1 + (sum(gains) / 14) / avg_loss), 1)
    hi, lo = max(closes), min(closes)
    pos52 = round((price - lo) / (hi - lo) * 100) if hi > lo else 50
    chg20 = round((price / closes[-21] - 1) * 100, 1)
    if rsi >= 70:
        verdict = "과열"
    elif rsi <= 30:
        verdict = "과매도"
    elif ma60 and price > ma20 > ma60:
        verdict = "상승추세"
    elif ma60 and price < ma20 < ma60:
        verdict = "하락추세"
    else:
        verdict = "횡보"
    return {"판정": verdict, "rsi": rsi, "ma20": round(ma20, 2),
            "ma60": round(ma60, 2) if ma60 else None,
            "pos52": pos52, "chg20": chg20,
            "hi52": round(hi, 2), "lo52": round(lo, 2), "price": round(price, 2),
            "spark": [round(c, 2) for c in closes[-30:]]}


# ---------------- 시장 풍향계 (매크로) ----------------

# 개별 종목을 보기 전에 "오늘 시장 분위기"를 먼저 알려주는 지표들.
# (지수코드, 화면이름, 엔드포인트 종류)
MACRO_SPECS = [
    (".VIX",  "공포지수", "us"),
    (".IXIC", "나스닥",   "us"),
    (".DJI",  "다우",     "us"),
    ("KOSPI", "코스피",   "kr"),
    ("KOSDAQ", "코스닥",  "kr"),
    ("FX_USDKRW", "원달러", "fx"),
]


def vix_mood(v):
    """VIX 수치 → 쉬운 말. 통상 20을 넘으면 시장이 불안하다고 본다."""
    if v is None:
        return None
    if v < 15:
        return "잠잠함"
    if v < 20:
        return "보통"
    if v < 30:
        return "불안"
    return "공포"


def fetch_macro():
    """VIX·주요 지수·환율 → 대시보드/브리핑용 리스트. 실패한 항목은 건너뛴다."""
    out = []
    for code, label, kind in MACRO_SPECS:
        try:
            if kind == "us":
                url = f"https://api.stock.naver.com/index/{code}/basic"
                d = requests.get(url, headers=NAVER_HEADERS, timeout=10).json()
            elif kind == "kr":
                url = f"https://m.stock.naver.com/api/index/{code}/basic"
                d = requests.get(url, headers=NAVER_HEADERS, timeout=10).json()
            else:
                url = f"https://api.stock.naver.com/marketindex/exchange/{code}"
                d = requests.get(url, headers=NAVER_HEADERS, timeout=10).json()
                d = d.get("exchangeInfo", {})
            val = _num(d.get("closePrice"))
            chg = _num(d.get("fluctuationsRatio"))
            if val is None:
                continue
            item = {"code": code, "label": label, "value": val, "change": chg}
            if code == ".VIX":
                item["mood"] = vix_mood(val)
            out.append(item)
        except (requests.RequestException, ValueError, KeyError, AttributeError) as e:
            log(f"[매크로] {label} 조회 실패: {type(e).__name__}")
    return out


# ---------------- 몬테카를로 (1년 뒤 범위) ----------------

def monte_carlo(closes, days=252, runs=10000):
    """과거 변동성으로 1년 뒤 주가 분포를 추정.

    수익률 '예측'이 아니라 변동폭 감각을 잡기 위한 것이라 추세(드리프트)는
    0으로 둔다. 과거 1년 수익률을 그대로 미래 기대수익률로 쓰면, 크게 오른
    종목일수록 미래도 더 오른다고 가정하는 꼴이라 결과가 심하게 왜곡된다.
    """
    if len(closes) < 60:
        return None
    rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:]) if a > 0 and b > 0]
    if len(rets) < 60:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    s0 = closes[-1]
    # 로그 드리프트도 0 → 중앙값이 정확히 '오늘 가격'이 된다.
    # 기대수익률을 0으로 두는 정석(마팅게일)은 중앙값이 현재가보다 아래로
    # 내려가는데, 그걸 '보통 시나리오'로 읽으면 하락 전망으로 오해한다.
    # 여기서 보여줄 것은 방향이 아니라 폭이므로 현재가를 가운데에 둔다.
    shock = sd * math.sqrt(days)
    rng = random.Random(20260101)          # 매 실행 같은 결과가 나오도록 고정
    ends = sorted(s0 * math.exp(shock * rng.gauss(0, 1)) for _ in range(runs))

    def pct(p):
        return round(ends[min(len(ends) - 1, int(len(ends) * p))], 2)

    vol = round(sd * math.sqrt(252) * 100, 1)
    return {"p5": pct(.05), "p25": pct(.25), "p50": pct(.50),
            "p75": pct(.75), "p95": pct(.95),
            "vol": vol, "now": round(s0, 2),
            # 변동성이 커질수록 정규분포 가정이 어긋난다.
            #  60% 이상 → 숫자는 보여주되 경고
            # 100% 이상 → 레버리지 ETF 영역. 매일 배수를 다시 맞추는 구조라
            #             1년 뒤 가격이 경로에 좌우돼(변동성 끌림) 이 모델이
            #             아예 성립하지 않는다. 숫자를 감추고 이유만 설명한다.
            "caution": vol >= 60, "unreliable": vol >= 100}


# ---------------- 5각형 능력치 ----------------

def _period_done(title):
    """'2026.06.30' / '2026.12.' → 그 회계연도가 이미 끝났는지.
    아직 안 끝난 기간은 증권사 추정치라 실적으로 쓰지 않는다."""
    m = re.match(r"(\d{4})\.(\d{2})", title or "")
    if not m:
        return False
    y, mo = int(m.group(1)), int(m.group(2))
    now = datetime.now()
    return (y, mo) < (now.year, now.month)


def fetch_financials(state, ticker):
    """네이버 연간 재무 → {매출성장률, ROE/ROA, 순이익률, 부채비율}. 없으면 None."""
    code = naver_code(state, ticker)
    if not code:
        return None
    if ticker.isdigit():
        url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
        d = requests.get(url, headers=NAVER_HEADERS, timeout=15).json().get("financeInfo") or {}
    else:
        url = f"https://api.stock.naver.com/stock/{code}/finance/annual"
        d = requests.get(url, headers=NAVER_HEADERS, timeout=15).json()
    rows = {r.get("title"): r.get("columns") or {} for r in d.get("rowList") or []}
    if not rows:
        return None
    periods = [t.get("title") for t in d.get("trTitleList") or []]
    done = sorted(p for p in periods if _period_done(p))
    if not done:
        return None

    def val(name, period):
        col = rows.get(name, {})
        # 컬럼 키는 '2026.06.30' 또는 '202612' 처럼 형식이 달라 둘 다 시도
        cell = col.get(period) or col.get(re.sub(r"\D", "", period)[:6]) or {}
        return _num(cell.get("value"))

    latest = done[-1]
    out = {"기준": latest}
    # 과거 PER — '지금이 이 회사치고 싼 편인가'를 재는 잣대
    out["per_hist"] = {p: v for p in done
                       if (v := val("PER", p)) and v > 0}
    sales_now = val("매출액", latest)
    if len(done) >= 2 and sales_now:
        prev = val("매출액", done[-2])
        if prev and prev > 0:
            out["매출성장률"] = round((sales_now / prev - 1) * 100, 1)
    out["ROE"] = val("ROE", latest)
    out["ROA"] = val("ROA", latest)
    out["부채비율"] = val("부채비율", latest)
    margin = val("순이익률", latest)
    if margin is None:
        net = val("당기순이익", latest)
        if net is not None and sales_now:
            margin = round(net / sales_now * 100, 1)
    out["순이익률"] = margin
    return out if any(v is not None for k, v in out.items() if k != "기준") else None


def _band(x, lo, hi):
    """x 를 lo(=0점) ~ hi(=100점) 구간에 선형으로 놓고 0~100 으로 자른다."""
    if x is None:
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals)) if vals else None


def compute_radar(row):
    """가치·수익성·성장성·모멘텀·안정성 5각형 (각 0~100). 자료 없는 축은 None."""
    fund = row.get("fund") or {}
    fin = row.get("fin") or {}
    tech = row.get("tech") or {}
    mc = row.get("mc") or {}

    per, pbr = _num(fund.get("per")), _num(fund.get("pbr"))
    가치 = _avg([_band(per, 45, 8) if per and per > 0 else None,
               _band(pbr, 7.0, 0.8) if pbr and pbr > 0 else None])

    수익성 = _avg([_band(fin.get("ROE"), 0, 25),
                _band(fin.get("ROA"), 0, 20),
                _band(fin.get("순이익률"), 0, 25)])

    성장성 = _band(fin.get("매출성장률"), -20, 20)
    성장성 = round(성장성) if 성장성 is not None else None

    모멘텀 = None
    if tech.get("pos52") is not None:
        모멘텀 = round(0.6 * tech["pos52"] + 0.4 * (_band(tech.get("chg20"), -20, 20) or 50))

    안정성 = _avg([_band(mc.get("vol"), 65, 15),          # 변동성 낮을수록 고득점
                _band(fin.get("부채비율"), 230, 30)])     # 빚 적을수록 고득점

    r = {"가치": 가치, "수익성": 수익성, "성장성": 성장성,
         "모멘텀": 모멘텀, "안정성": 안정성}
    return r if sum(v is not None for v in r.values()) >= 3 else None


def naver_code(state, ticker):
    """티커 → 네이버 reutersCode (state에 캐시). 한국 종목은 숫자코드 그대로."""
    if ticker.isdigit():
        return ticker
    cache = state.setdefault("naver", {})
    if ticker in cache:
        return cache[ticker]
    url = (f"https://m.stock.naver.com/front-api/search/autoComplete"
           f"?query={ticker}&target=stock")
    r = requests.get(url, headers=NAVER_HEADERS, timeout=15)
    for it in r.json()["result"]["items"]:
        if it.get("category") == "stock" and it.get("code", "").upper() == ticker.upper():
            cache[ticker] = it["reutersCode"]
            return cache[ticker]
    return None


def fetch_fundamentals(state, ticker):
    """네이버 증권에서 재무지표. 없는 값은 None."""
    code = naver_code(state, ticker)
    if not code:
        return None
    if ticker.isdigit():
        url = f"https://m.stock.naver.com/api/stock/{code}/integration"
        infos = requests.get(url, headers=NAVER_HEADERS, timeout=15).json().get("totalInfos", [])
    else:
        url = f"https://api.stock.naver.com/stock/{code}/basic"
        infos = requests.get(url, headers=NAVER_HEADERS, timeout=15).json().get("stockItemTotalInfos", [])
    m = {t["code"]: t.get("value") for t in infos}
    return {"per": m.get("per"), "pbr": m.get("pbr"), "eps": m.get("eps"),
            "div": m.get("dividendYieldRatio"), "mcap": m.get("marketValue")}


def _ask(config, prompt, max_tokens=900):
    """Claude 단발 호출 → 텍스트 반환. 실패 시 None"""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": config.get("ANTHROPIC_API_KEY", ""),
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": config.get("AI모델", "claude-haiku-4-5-20251001"),
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        if r.status_code != 200:
            log(f"  토론 API 오류 {r.status_code}: {r.text[:120]}")
            return None
        return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    except (requests.RequestException, KeyError, ValueError) as e:
        log(f"  토론 호출 실패: {e}")
        return None


def valuation(row):
    """지금 주가가 이 회사치고 싼가 비싼가 (0~100, 높을수록 쌈).

    두 잣대를 쓴다. 절대적인 PER 숫자만 보면 업종별로 기준이 달라
    "성장주는 무조건 비싸다"는 잘못된 결론이 나오기 때문이다.

      ① 성장 대비 (PEG) — 버는 돈 대비 주가 ÷ 매출 성장률
         빨리 크는 회사는 비싸 보여도 쌀 수 있다. 1보다 작으면 싼 편.
      ② 자기 이력 대비 — 지금 PER 이 과거 몇 년 평균보다 낮은가
         같은 회사끼리 비교하는 것이라 업종 차이를 자동으로 걷어낸다.
    """
    fund, fin = row.get("fund") or {}, row.get("fin") or {}
    per = _num(fund.get("per"))
    if not per or per <= 0:
        return None
    parts, why = [], []

    growth = fin.get("매출성장률")
    peg = None
    if growth and growth > 0:
        peg = round(per / growth, 2)
        # PEG 0.5 → 100점, 3.0 → 0점
        parts.append(_band(peg, 3.0, 0.5))
        why.append(f"매출이 한 해 {growth}% 크는 것에 비하면 "
                   f"{'싼' if peg < 1 else '보통인' if peg < 2 else '비싼'} 편이에요")

    hist = [v for k, v in (fin.get("per_hist") or {}).items()]
    per_avg = round(sum(hist) / len(hist), 1) if len(hist) >= 2 else None
    per_vs = None
    if per_avg and per_avg > 0:
        per_vs = round((per / per_avg - 1) * 100)
        # 과거 평균의 70% → 100점, 150% → 0점
        parts.append(_band(per / per_avg, 1.5, 0.7))
        why.append(f"이 회사가 예전에 받던 값({per_avg}배)보다 지금이 {abs(per_vs)}% "
                   f"{'싸요' if per_vs < 0 else '비싸요'} (지금 {per}배)")

    if not parts:
        return None
    score = _avg(parts)
    verdict = "저평가" if score >= 65 else "고평가" if score <= 35 else "적정"
    return {
        "판정": verdict, "점수": score, "per": per, "peg": peg,
        "per_avg": per_avg, "per_vs": per_vs, "성장률": growth, "근거": why,
        # 두 잣대가 크게 엇갈리면 평균 한 숫자로 뭉개면 안 된다.
        # (예: 성장 대비는 싼데 자기 과거보다는 훨씬 비싼 경우)
        "상충": bool(len(parts) == 2 and abs(parts[0] - parts[1]) >= 45),
        # 매출 성장률로 계산한 PEG 는 이익 기준보다 후하게 나온다는 점을 표시
        "peg_기준": "매출" if peg else None,
    }


def price_levels(row, config):
    """목표 매수가·매도가·손절가를 계산한다 (AI가 지어내지 않도록 숫자는 여기서).

    기준은 '손실 폭을 먼저 정하고 목표를 그에 비례해 잡는다'는 원칙.
      손절가 = 지금가 − ATR × 배수      ← 여기까지 틀리면 인정하고 나온다
      R      = 지금가 − 손절가          ← 한 번에 감수할 폭
      목표가 = 지금가 + R × 2 (그리고 ×3)
    목표를 먼저 정하고 손절을 끼워 맞추면 손익비가 무너지므로 순서를 지킨다.
    52주 고가·이동평균은 '이 근처에서 걸리기 쉽다'는 참고선으로만 함께 넘긴다.
    """
    tech, atr = row.get("tech") or {}, row.get("atr")
    price = tech.get("price")
    if not price or not atr or atr <= 0:
        return None
    k = float(config.get("손절_ATR배수", 2))
    stop = price - atr * k
    if stop <= 0:
        return None
    R = price - stop
    krw = str(row.get("ticker", "")).isdigit()
    rnd = (lambda v: round(v)) if krw else (lambda v: round(v, 2))

    hi52 = tech.get("hi52")
    t1 = price + R * 2
    return {
        "지금가": rnd(price),
        "손절가": rnd(stop),
        "R": rnd(R),
        # 분할매수: 지금 조금, 눌리면 더 (한 번에 다 담지 않기 위함)
        "매수1": rnd(price),
        "매수2": rnd(price - R * 0.5),
        "목표1": rnd(t1),
        "목표2": rnd(price + R * 3),
        "손익비": round((R * 2) / R, 1),          # 1차 목표 기준 = 2.0 고정
        # 목표가 52주 최고를 넘는지. 다만 이건 대개 '목표가 커서'가 아니라
        # '이미 최고가 근처라서' 생긴다 — 15종목을 재보니 넘는 쪽의 목표(+13%)가
        # 오히려 안 넘는 쪽(+22%)보다 작았고, 갈린 건 52주 위치(86% vs 47%)였다.
        # 그래서 최고가 부근인지 여부를 함께 넘겨 문구를 다르게 쓴다.
        "신고가필요": bool(hi52 and t1 > hi52),
        "고점부근": bool((tech.get("pos52") or 0) >= 85),
        "목표퍼센트": round((t1 / price - 1) * 100),
        # 변동성이 큰 종목은 R 이 커져 목표가 몇 달짜리 큰 폭이 된다.
        # 며칠 안에 닿을 값으로 착각하지 않도록 표시.
        "큰폭": bool((t1 / price - 1) * 100 >= 25),
        "pos52": tech.get("pos52"),
        "ma20": tech.get("ma20"), "ma60": tech.get("ma60"),
        "hi52": hi52, "lo52": tech.get("lo52"),
        "krw": krw,
    }


def _fmt_px(v, krw):
    if v is None:
        return "-"
    return f"{round(v):,}원" if krw else f"${v:,.2f}"


def level_notes(lv):
    """목표가에 붙일 주의 문구. 없으면 빈 목록."""
    if not lv:
        return []
    f = lambda v: _fmt_px(v, lv.get("krw"))
    out = []
    if lv.get("신고가필요"):
        if lv.get("고점부근"):
            # 이미 최고가 근처면 목표가 신고가인 게 당연하다. 겁줄 일이 아니다.
            out.append(f"이미 52주 최고({f(lv['hi52'])}) 부근이라 목표는 자연히 신고가예요")
        else:
            out.append(f"52주 최고({f(lv['hi52'])})를 넘어야 닿는 값이에요")
    if lv.get("큰폭"):
        out.append(f"많이 흔들리는 종목이라 목표가 {lv['목표퍼센트']}%로 크게 잡혔어요. "
                   f"며칠이 아니라 몇 달 볼 값이에요")
    return out


def pick_debate_targets(team, limit=2):
    """의견이 갈리는 종목 우선 선정 — 세부점수 편차가 큰 순
    (예: 뉴스 100점인데 기술 28점 → 토론 가치가 높음)"""
    scored = []
    for t in team:
        s = t.get("점수") or {}
        vals = [s.get(k) for k in ("뉴스", "기술", "재무", "민심") if s.get(k) is not None]
        if len(vals) < 3:
            continue
        spread = max(vals) - min(vals)
        # 강세/약세 신호도 가산점 (방향이 뚜렷한 종목은 검증 가치가 있음)
        if t.get("신호") in ("강세", "약세"):
            spread += 10
        scored.append((spread, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:limit]]


def _debate_brief(row, feed, cutoff):
    """토론용 종목 데이터 요약 (토큰 절약을 위해 압축)"""
    s, tech, fund = row.get("점수") or {}, row.get("tech") or {}, row.get("fund") or {}
    mc, size, fin = row.get("mc") or {}, row.get("size") or {}, row.get("fin") or {}
    recent = [n for n in feed if n["ticker"] == row["ticker"]
              and (n.get("createdAt") or "") >= cutoff][:6]
    news = " / ".join(f"[{n['verdict']}]{clip(n['title'], 28)}" for n in recent) or "없음"
    lines = [
        f"종목: {row['name']}({row['ticker']})",
        f"점수: 종합 {s.get('종합','?')} (뉴스 {s.get('뉴스','?')} / 기술 {s.get('기술','?')} / "
        f"재무 {s.get('재무','?')} / 민심 {s.get('민심','?')})",
        f"기술: {tech.get('판정','?')}, RSI {tech.get('rsi','?')}, 20일 {tech.get('chg20','?')}%, "
        f"52주위치 {tech.get('pos52','?')}%",
        f"재무: PER {fund.get('per') or '-'}, PBR {fund.get('pbr') or '-'}, 배당 {fund.get('div') or '-'}",
    ]
    if fin:
        lines.append(f"실적: 매출성장 {fin.get('매출성장률','-')}%, ROE {fin.get('ROE','-')}, "
                     f"순이익률 {fin.get('순이익률','-')}%, 부채비율 {fin.get('부채비율','-')}%")
    val = row.get("val")
    if val:
        lines.append(f"싼가 비싼가: {val['판정']}({val['점수']}점) — " + " / ".join(val["근거"])
                     + (" ※ 두 잣대가 엇갈림" if val.get("상충") else ""))
    if mc and mc.get("now"):
        # 절대 가격만 주면 '1.2배~4배' 처럼 배수로 오해한다. 현재가 대비 %를 함께 준다.
        now = mc["now"]
        lo = round((mc["p5"] / now - 1) * 100)
        hi = round((mc["p95"] / now - 1) * 100)
        lines.append(f"위험: 연 변동성 {mc.get('vol')}%. 현재가 {now} 기준 1년 뒤 열에 아홉은 "
                     f"{mc['p5']}({lo}%) ~ {mc['p95']}(+{hi}%) 사이"
                     + (" (변동성이 너무 커서 이 추정은 신뢰도 낮음)" if mc.get("caution") else ""))
    if size.get("qty"):
        lines.append(f"권장 규모: {size['qty']}주 (계좌의 {size.get('weight')}%)")
    lv = row.get("levels")
    if lv:
        lines.append(
            f"계산된 가격대(이 숫자만 쓸 것): 지금 {lv['지금가']} / "
            f"1차매수 {lv['매수1']} / 2차매수 {lv['매수2']} / "
            f"1차목표 {lv['목표1']} / 2차목표 {lv['목표2']} / 손절 {lv['손절가']}\n"
            f"참고선: 20일평균 {lv.get('ma20')}, 60일평균 {lv.get('ma60')}, "
            f"52주 최고 {lv.get('hi52')}, 52주 최저 {lv.get('lo52')}")
    lines.append(f"민심: {row.get('민심','?')} (시간당 댓글 {row.get('heat','?')}개)")
    lines.append(f"최근 뉴스: {news}")
    return "\n".join(lines)


def run_debate(config, row, feed):
    """불리(강세) vs 베어(약세) 토론 → 부엉(중재) 결론. Claude 3회 호출."""
    cutoff = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    row = dict(row, levels=price_levels(row, config))
    brief = _debate_brief(row, feed, cutoff)
    name = row["name"]

    # 이 규칙은 프롬프트 '맨 끝'에 붙인다. 중간에 두면 뒤에 온 지시에 밀려
    # 실제로 무시됐다(판정문에 RSI·PER·PBR·밸류에이션이 그대로 나왔음).
    PLAIN = (
        "\n\n<반드시 지킬 말투 규칙 — 이 규칙이 다른 무엇보다 우선한다>\n"
        "주식을 처음 배우는 사람에게 말하듯 쉬운 우리말만 쓴다.\n"
        "다음 단어는 절대 쓰지 말 것: RSI, PER, PBR, ROE, ROA, PEG, 밸류에이션, "
        "모멘텀, 멀티플, 컨센서스, 포지션, 리스크, 펀더멘털.\n"
        "대신 이렇게 바꿔 쓴다:\n"
        "  RSI 75 → '한 달 새 가파르게 올라 숨이 찬 상태'\n"
        "  PER 34배 → '버는 돈의 34배 가격'\n"
        "  PBR 28배 → '회사가 가진 재산의 28배 가격'\n"
        "  ROE 10% → '가진 돈으로 한 해 10% 를 벌어들임'\n"
        "  밸류에이션이 높다 → '주가가 비싸다'\n"
        "  모멘텀이 강하다 → '오르는 힘이 세다'\n"
        "숫자는 그대로 인용하되 반드시 뜻을 한마디로 붙인다.\n")

    bull = _ask(config,
        f"너는 주식 애널리스트 '불리'다. 아래 데이터로 {name}이 <b>좋아 보이는 이유</b>를 편다.\n"
        f"규칙: 데이터에 있는 숫자를 근거로 쓸 것. 3개 논점, 각 1~2문장. "
        f"과장 금지, 없는 사실 지어내지 말 것. 번호 목록으로만 답하라.\n"
        f"\n{brief}" + PLAIN)
    if not bull:
        return None

    bear = _ask(config,
        f"너는 주식 애널리스트 '베어'다. 아래는 {name}에 대한 낙관론자의 주장이다.\n"
        f"<b>각 논점을 구체적으로 반박</b>하라. 일반론이 아니라 상대가 든 근거의 약점을 짚을 것.\n"
        f"3개 반박, 각 1~2문장. 번호 목록으로만 답하라.\n"
        f"\n[데이터]\n{brief}\n\n[낙관론자 주장]\n{bull}" + PLAIN)
    if not bear:
        return None

    # 리스크 관리자 — 논문(TradingAgents)의 Risk Management Team 역할.
    # 방향이 맞아도 크기가 틀리면 계좌가 망가지므로 '얼마나'를 따로 본다.
    risk = _ask(config,
        f"너는 리스크 관리자 '가디'다. {name}을 사고파는 게 아니라 <b>얼마나 위험한지</b>만 본다.\n"
        f"다음 3가지를 각 1문장으로 짚어라. 번호 목록으로만 답하라.\n"
        f"1) 이 종목이 얼마나 흔들리는지, 최악의 경우 얼마나 잃을 수 있는지\n"
        f"2) 지금 들어가면 계좌에서 어느 정도 비중이 적당한지\n"
        f"3) 판단이 틀렸다고 인정하고 나와야 하는 지점\n"
        f"\n{brief}" + PLAIN,
        max_tokens=500)

    judge = _ask(config,
        f"너는 리서치 매니저 '부엉'이다. 아래 토론과 위험 검토를 종합해 <b>결론</b>을 내려라.\n"
        f"판단은 반드시 다음 중 하나: 적극매수 / 매수 / 홀드 / 매도 / 적극매도\n"
        f"근거가 약하거나 양쪽이 팽팽하면 억지로 방향을 잡지 말고 '홀드'로 두어라.\n"
        f'형식(JSON만): {{"판단":"위 5개 중 하나","확신도":"높음|보통|낮음",'
        f'"우세":"강세|약세|팽팽","한줄":"판단 이유 한 문장(45자 이내)",'
        f'"핵심근거":["근거1","근거2","근거3"],'
        f'"반대로볼근거":"이 판단이 틀릴 수 있는 가장 강한 이유 한 문장",'
        f'"무효화조건":"이런 일이 생기면 판단을 뒤집어야 한다는 구체적 조건 한 문장",'
        f'"가격근거":"제시한 가격대를 그렇게 본 이유 한 문장(참고선과 엮어서)",'
        f'"지켜볼것":"관전 포인트 한 문장(40자 이내)"}}\n'
        f"<가격 규칙> 가격은 [데이터]의 '계산된 가격대'에 있는 숫자를 그대로 쓴다. "
        f"직접 계산하거나 새 숫자를 만들지 마라. 가격근거에서만 참고선을 언급하라.\n\n"
        f"[데이터]\n{brief}\n\n[강세]\n{bull}\n\n[약세]\n{bear}\n\n[위험 검토]\n{risk or '없음'}"
        + PLAIN,
        max_tokens=1200)
    if not judge:
        return None

    m = re.search(r"\{.*\}", judge, re.DOTALL)
    verdict = {}
    if m:
        try:
            verdict = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    call = verdict.get("판단", "")
    if call not in ("적극매수", "매수", "홀드", "매도", "적극매도"):
        call = "홀드"        # 형식을 벗어나면 가장 보수적인 쪽으로

    price = None
    for s in (row.get("tech") or {}).get("spark") or []:
        price = s            # 판단 시점 가격 — 나중에 맞았는지 채점하려고 남긴다

    return {
        "ticker": row["ticker"], "name": name,
        "score": (row.get("점수") or {}).get("종합"),
        "bull": bull, "bear": bear, "risk": risk,
        "levels": row.get("levels"),
        "가격근거": verdict.get("가격근거", ""),
        "판단": call,
        "확신도": verdict.get("확신도", "보통"),
        "우세": verdict.get("우세", "팽팽"),
        "한줄": verdict.get("한줄", ""),
        "핵심근거": [str(x) for x in (verdict.get("핵심근거") or [])][:3],
        "반대로볼근거": verdict.get("반대로볼근거", ""),
        "무효화조건": verdict.get("무효화조건", ""),
        "핵심쟁점": verdict.get("핵심쟁점", ""),
        "결론": verdict.get("한줄", ""),      # 예전 대시보드 호환
        "지켜볼것": verdict.get("지켜볼것", ""),
        "price": price,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


CALL_EMOJI = {"적극매수": "🟢🟢", "매수": "🟢", "홀드": "⚪",
              "매도": "🔴", "적극매도": "🔴🔴"}


def format_verdict(d):
    """토론 결론을 텔레그램 메시지로"""
    P = [f"🥊 <b>{esc(d['name'])}</b> 종합 판단",
         f"{CALL_EMOJI.get(d.get('판단'), '⚪')} <b>{esc(d.get('판단', '홀드'))}</b>"
         f"  ·  확신도 {esc(d.get('확신도', '보통'))}"
         + (f"  ·  종합 {d['score']}점" if d.get("score") is not None else "")]
    if d.get("한줄"):
        P.append(f"\n{esc(d['한줄'])}")
    for i, r in enumerate(d.get("핵심근거") or [], 1):
        P.append(f"{i}. {esc(r)}")
    lv = d.get("levels")
    if lv:
        f = lambda v: _fmt_px(v, lv.get("krw"))
        P.append(f"\n💵 <b>가격대</b>  <i>(지금 {f(lv['지금가'])})</i>")
        P.append(f"사기: {f(lv['매수1'])} · 더 눌리면 {f(lv['매수2'])}")
        P.append(f"팔기: {f(lv['목표1'])} · 더 가면 {f(lv['목표2'])}")
        for note in level_notes(lv):
            P.append(f"  <i>{note}</i>")
        P.append(f"손절: {f(lv['손절가'])}  <i>(먹을 것이 잃을 것의 {lv['손익비']}배)</i>")
        if d.get("가격근거"):
            P.append(f"<i>{esc(d['가격근거'])}</i>")
    if d.get("반대로볼근거"):
        P.append(f"\n⚠️ <b>반대 시각</b>\n{esc(d['반대로볼근거'])}")
    if d.get("무효화조건"):
        P.append(f"\n↩︎ <b>이러면 판단이 틀린 것</b>\n{esc(d['무효화조건'])}")
    if d.get("지켜볼것"):
        P.append(f"\n👀 {esc(d['지켜볼것'])}")
    P.append("\n<i>AI 의견이에요.</i>")
    return "\n".join(P)


def run_debates(config, team, feed, tickers=None):
    """토론 실행. tickers를 주면 그 종목만, 없으면 의견이 갈리는 종목을 자동 선정"""
    if tickers:
        by_tk = {t["ticker"].upper(): t for t in team}
        targets, missing = [], []
        for tk in tickers:
            row = by_tk.get(tk.upper())
            (targets.append(row) if row else missing.append(tk))
        if missing:
            log(f"진단 데이터가 없는 종목은 건너뜁니다: {', '.join(missing)}")
        if not targets:
            log("토론할 종목이 없습니다. config.json의 '종목'에 있는 티커인지 확인하세요.")
            return []
    else:
        if not config.get("토론_사용", True):
            return []
        targets = pick_debate_targets(team, int(config.get("토론_종목수", 2)))
    out = []
    for row in targets:
        log(f"🥊 토론 시작: {row['name']} (종합 {(row.get('점수') or {}).get('종합')}점)")
        d = run_debate(config, row, feed)
        if d:
            log(f"   → {d['판단']} (확신 {d['확신도']}) · {clip(d.get('한줄',''), 40)}")
            out.append(d)
        time.sleep(0.5)
    return out


def analyze_team(config, rows):
    """타로(기술)·디아나(재무) 한마디를 Claude 한 번 호출로 전 종목 생성.
    rows: [{ticker,name,tech,fund}] → {ticker: {"타로":..,"디아나":..,"등급":..}}"""
    lines = []
    for r in rows:
        t, f = r.get("tech") or {}, r.get("fund") or {}
        lines.append(
            f"- {r['name']}({r['ticker']}): 기술[판정 {t.get('판정','?')}, RSI {t.get('rsi','?')}, "
            f"20일등락 {t.get('chg20','?')}%, 52주위치 {t.get('pos52','?')}%] "
            f"재무[PER {f.get('per') or '-'}, PBR {f.get('pbr') or '-'}, EPS {f.get('eps') or '-'}, "
            f"배당 {f.get('div') or '-'}, 시총 {f.get('mcap') or '-'}] "
            f"민심[시간당 댓글 {r.get('heat', '?')}개({r.get('민심','?')}), 인기순위 {r.get('rank') or '100위밖'}] "
            f"최근뉴스[{r.get('뉴스요약') or '없음'}] "
            f"점수[뉴스 {(r.get('점수') or {}).get('뉴스','?')}, 기술 {(r.get('점수') or {}).get('기술','?')}, "
            f"재무 {(r.get('점수') or {}).get('재무','?')}, 민심 {(r.get('점수') or {}).get('민심','?')}, "
            f"종합 {(r.get('점수') or {}).get('종합','?')}점({r.get('신호','?')})]")
    prompt = (
        "너는 픽셀 게임 속 주식 애널리스트 팀이다. 타로는 기술적 분석 담당(차트쟁이, 담백한 말투), "
        "디아나는 재무제표 담당(꼼꼼한 말투), 바이브는 커뮤니티 민심 담당(힙한 말투). "
        "아래 각 종목 데이터를 보고 JSON 배열로만 답하라.\n"
        "형식: [{\"ticker\":\"...\",\"타로\":\"기술적 관점 한마디(40자 이내)\","
        "\"디아나\":\"재무 관점 한마디(40자 이내)\",\"등급\":\"튼튼|보통|주의\","
        "\"바이브\":\"민심 관점 한마디(30자 이내)\",\"총평\":\"팀 종합 한줄평(60자 이내)\"}]\n"
        "등급은 재무 관점 종합평가. ETF처럼 재무지표가 없으면 디아나는 지수/상품 성격을 언급하고 등급은 \"보통\".\n"
        "바이브는 댓글 수와 인기순위로 개미들 관심도를 표현하라(과열이면 경고).\n"
        "각 종목에는 팀의 공식 점수(0~100)가 이미 계산돼 있다. "
        "총평은 종합 점수·신호와 일관되게 핵심 근거를 요약하되, "
        "매수/매도 같은 투자 지시어는 쓰지 말고 상태 진단으로 표현하라.\n"
        "<말투 규칙> 주식을 처음 배우는 사람도 알아듣게 쉬운 우리말로 써라. "
        "RSI·PER·PBR·밸류에이션·모멘텀·멀티플 같은 전문용어를 그대로 쓰지 말고 뜻을 풀어 쓸 것. "
        "예: 'RSI 73' → '최근 너무 가파르게 올라 과열', 'PER 27배' → '버는 돈에 비해 주가가 비쌈', "
        "'저평가' → '값이 싼 편'. 숫자는 그대로 인용해도 되지만 무슨 뜻인지 한마디로 붙여라.\n\n"
        + "\n".join(lines))
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": config.get("ANTHROPIC_API_KEY", ""),
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": config.get("AI모델", "claude-haiku-4-5-20251001"),
                  "max_tokens": 6000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        if r.status_code != 200:
            log(f"팀 분석 API 오류 {r.status_code}: {r.text[:150]}")
            return {}
        body = r.json()
        if body.get("stop_reason") == "max_tokens":
            log("팀 분석 경고: 응답이 길이 제한에 잘림")
        text = "".join(b.get("text", "") for b in body["content"])
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            log(f"팀 분석 파싱 실패 — 응답 앞부분: {text[:120]}")
            return {}
        return {x["ticker"]: x for x in json.loads(m.group(0))}
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as e:
        log(f"팀 분석 실패: {e}")
        return {}


def _num(s):
    """'40.84배', '0.66%', '-1.65' 같은 값에서 숫자 추출.

    음수 부호를 반드시 살려야 한다 — 등락률·성장률·적자 종목의 ROE는
    마이너스가 정상이고, 부호를 잃으면 하락을 상승으로 읽는다.
    자릿수 없는 '-'(자료 없음)나 '.'는 None 으로 떨어진다.
    """
    if s is None:
        return None
    m = re.search(r"-?\d[\d.]*", str(s).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def score_row(row, feed, cutoff):
    """애널리스트 4팀 점수(0~100) + 종합 점수 계산 (일관된 공식 기반)"""
    clamp = lambda v: max(0, min(100, round(v)))
    # 📰 뉴스팀 (불리·베어): 최근 3일 호재/악재 중요도 가중
    recent = [n for n in feed if n["ticker"] == row["ticker"] and (n.get("createdAt") or "") >= cutoff]
    news = 50 + 3 * sum(n["importance"] for n in recent if n["verdict"] == "호재") \
              - 3 * sum(n["importance"] for n in recent if n["verdict"] == "악재")
    # 🦊 타로 (기술)
    t = row.get("tech") or {}
    verdict_pts = {"상승추세": 20, "과매도": 10, "횡보": 0, "과열": -10, "하락추세": -20}
    tech = 50 + verdict_pts.get(t.get("판정"), 0) \
              + max(-15, min(15, t.get("chg20") or 0)) \
              + ((t.get("pos52") or 50) - 50) / 5
    # 🐰 디아나 (재무)
    f = row.get("fund") or {}
    per, pbr, div = _num(f.get("per")), _num(f.get("pbr")), _num(f.get("div"))
    fund = 50
    if per is not None:
        fund += 15 if per < 15 else 8 if per < 25 else 0 if per < 40 else -15
    if pbr is not None:
        fund += 5 if pbr < 2 else -5 if pbr > 10 else 0
    if div is not None and div >= 2:
        fund += 5
    # 🎧 바이브 (민심) — 과열은 역지표로 감점
    vibe = {"조용": 50, "보통": 60, "시끌": 50, "과열": 35}.get(row.get("민심"), 50)
    if row.get("rank") and row["rank"] <= 10:
        vibe -= 5
    scores = {"뉴스": clamp(news), "기술": clamp(tech), "재무": clamp(fund), "민심": clamp(vibe)}
    scores["종합"] = clamp(0.30 * scores["뉴스"] + 0.30 * scores["기술"]
                          + 0.25 * scores["재무"] + 0.15 * scores["민심"])
    return scores


def build_team(config, state, feed=None):
    """전 종목 기술+재무+민심+뉴스 종합 분석 → (feed.js용 rows, {ticker:(dates,closes)})"""
    rows, candle_map = [], {}
    feed = feed if feed is not None else load_feed()
    cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    try:
        ranks = fetch_hot_ranks()
    except (requests.RequestException, KeyError):
        ranks = {}
    for ticker in [str(t) for t in config.get("종목", [])]:
        if ticker not in state["codes"]:
            continue
        page = state["codes"][ticker]["page"]
        row = {"ticker": ticker, "name": state["names"].get(ticker, ticker)}
        # 최근 3일 뉴스 요약 (종합 신호 판단용)
        recent = [n for n in feed if n["ticker"] == ticker and (n.get("createdAt") or "") >= cutoff]
        if recent:
            g = sum(1 for n in recent if n["verdict"] == "호재")
            b = sum(1 for n in recent if n["verdict"] == "악재")
            tops = sorted((n for n in recent if n["verdict"] != "중립"),
                          key=lambda n: -n["importance"])[:2]
            row["뉴스요약"] = (f"호재{g} 악재{b}" +
                            "".join(f" / {n['verdict']}:{n['title'][:25]}" for n in tops))
        else:
            row["뉴스요약"] = None
        try:
            dates, closes, highs, lows, fx = fetch_candles(page)
            candle_map[ticker] = (dates, closes)
            row["tech"] = compute_tech(closes)
            row["mc"] = monte_carlo(closes)
            atr = compute_atr(closes, highs, lows)
            row["atr"] = atr
            # 계좌금액이 원화이므로 가격·ATR을 원화로 환산해 수량 계산
            row["size"] = position_size(config, closes[-1] * fx,
                                        atr * fx if atr else None)
            if row["size"] and fx != 1:   # 표시용 손절가는 원래 통화로 되돌림
                row["size"]["stop"] = round(row["size"]["stop"] / fx, 2)
                row["size"]["stop_dist"] = round(row["size"]["stop_dist"] / fx, 2)
        except (requests.RequestException, KeyError, IndexError) as e:
            log(f"[{ticker}] 캔들 조회 실패: {e}")
            row["tech"] = None
        try:
            row["fund"] = fetch_fundamentals(state, ticker)
        except (requests.RequestException, KeyError, ValueError) as e:
            log(f"[{ticker}] 재무 조회 실패: {e}")
            row["fund"] = None
        try:
            row["fin"] = fetch_financials(state, ticker)
        except (requests.RequestException, KeyError, ValueError) as e:
            log(f"[{ticker}] 연간 재무 조회 실패: {e}")
            row["fin"] = None
        try:
            row["heat"] = fetch_community_heat(state, ticker)
        except (requests.RequestException, KeyError, ValueError) as e:
            log(f"[{ticker}] 커뮤니티 조회 실패: {e}")
            row["heat"] = None
        row["rank"] = ranks.get(page)
        row["민심"] = vibe_verdict(row["heat"])
        row["radar"] = compute_radar(row)
        row["val"] = valuation(row)
        row["점수"] = score_row(row, feed, cutoff)
        # 신호는 종합 점수에서 자동 결정 (65↑ 강세, 40↓ 약세)
        total = row["점수"]["종합"]
        row["신호"] = "강세" if total >= 65 else "약세" if total <= 40 else "중립"
        rows.append(row)
    comments = analyze_team(config, rows)
    for row in rows:
        c = comments.get(row["ticker"], {})
        row["타로"] = c.get("타로", "")
        row["디아나"] = c.get("디아나", "")
        row["등급"] = c.get("등급", "보통")
        row["바이브"] = c.get("바이브", "")
        row["총평"] = c.get("총평", "")
    return rows, candle_map


def fetch_prices(page_codes):
    """종목페이지 코드 목록 → {코드: (현재가, 등락률%)}"""
    url = ("https://wts-info-api.tossinvest.com/api/v1/product/stock-prices"
           "?meta=true&productCodes=" + ",".join(page_codes))
    r = requests.get(url, headers=TOSS_HEADERS, timeout=15)
    r.raise_for_status()
    out = {}
    for p in r.json()["result"]:
        close, base = p.get("close"), p.get("base")
        if close and base:
            out[p["productCode"]] = (close, round((close - base) / base * 100, 2))
    return out


# ---------------- Claude AI 분석 ----------------

def analyze_news(config, stock_name, ticker, title, content):
    """뉴스 1건을 Claude에게 분석시켜 dict 반환. 실패하면 None."""
    api_key = config.get("ANTHROPIC_API_KEY", "")
    prompt = (
        f"다음은 '{stock_name}({ticker})' 종목 관련 뉴스입니다.\n\n"
        f"제목: {title}\n"
        f"내용: {content[:1200]}\n\n"
        f"이 뉴스가 해당 종목 주가에 미칠 영향을 판단해서 아래 JSON 형식으로만 답하세요. 다른 말은 하지 마세요.\n"
        f'{{"판정":"호재" 또는 "악재" 또는 "중립","중요도":1~5 정수,"이유":"한 문장 요약"}}\n\n'
        f"중요도 기준: 5=주가에 매우 큰 영향(어닝 서프라이즈/쇼크, 대형 M&A, 대규모 수주·계약, 소송 판결 등), "
        f"3=의미 있는 영향(신제품, 애널리스트 목표가 변경, 업황 뉴스), 1=거의 무관(단순 시황 언급, 광고성 기사)."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.get("AI모델", "claude-haiku-4-5-20251001"),
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            log(f"  Claude API 오류 {r.status_code}: {r.text[:200]}")
            return None
        text = "".join(b.get("text", "") for b in r.json()["content"])
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        result = json.loads(m.group(0))
        verdict = str(result.get("판정", "중립"))
        if verdict not in ("호재", "악재", "중립"):
            verdict = "중립"
        importance = int(result.get("중요도", 1))
        importance = max(1, min(5, importance))
        return {"판정": verdict, "중요도": importance, "이유": str(result.get("이유", ""))}
    except requests.RequestException as e:
        log(f"  Claude API 연결 실패: {e}")
        return None
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        log(f"  분석 결과 파싱 실패: {e}")
        return None


# ---------------- 텔레그램 ----------------

def send_telegram(config, text):
    token = config.get("텔레그램_토큰", "")
    chat_id = config.get("텔레그램_채팅ID", "")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"  텔레그램 전송 실패 {r.status_code}: {r.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        log(f"  텔레그램 연결 실패: {e}")
        return False


# ---------------- 텔레그램 명령 처리 ----------------

HELP_TEXT = (
    "🤖 <b>이렇게 시켜보세요</b>\n\n"
    "<code>/새로고침</code>  최신 시세로 대시보드 갱신\n"
    "<code>/토론 NVDA</code>  그 종목을 불리 vs 베어가 토론\n"
    "<code>/브리핑</code>  지금 바로 아침 브리핑 받기\n\n"
    "<b>감시 종목 바꾸기</b>\n"
    "<code>/종목</code>  지금 보고 있는 종목 목록\n"
    "<code>/종목추가 PLTR</code>  새 종목 넣기\n"
    "<code>/종목삭제 PLTR</code>  빼기\n"
    "  · 미국은 티커(PLTR), 한국은 6자리 코드(005930)\n"
    "  · 여러 개는 쉼표로: <code>/종목추가 PLTR,AMD</code>\n\n"
    "<code>/도움말</code>  이 안내 다시 보기\n"
    "슬래시나 띄어쓰기는 틀려도 알아들어요."
)


# 명령 이름 → 대표 이름. 슬래시가 없어도, 영어로 써도 알아듣는다.
COMMAND_ALIASES = {
    "새로고침": "새로고침", "갱신": "새로고침", "refresh": "새로고침",
    "토론": "토론", "debate": "토론", "판단": "토론", "분석": "토론",
    "브리핑": "브리핑", "briefing": "브리핑", "리포트": "브리핑",
    "도움말": "도움말", "help": "도움말", "start": "도움말", "명령어": "도움말",
    "종목": "종목", "목록": "종목", "list": "종목",
    "종목추가": "종목추가", "추가": "종목추가", "add": "종목추가",
    "종목삭제": "종목삭제", "삭제": "종목삭제", "빼기": "종목삭제", "remove": "종목삭제",
}


def normalize_command(text):
    """사용자가 보낸 말 → (명령, 인자). 명령이 아니면 None.

    '/브리핑', '/ 브리핑'(슬래시 뒤 띄어쓰기), '브리핑'(슬래시 없음),
    '/토론@내봇 NVDA' 를 모두 같은 것으로 본다. 폰에서 치다 보면
    띄어쓰기가 끼거나 슬래시를 빠뜨리기 쉬워서 너그럽게 받는다.
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("/"):
        t = t[1:].lstrip()          # '/ 브리핑' 도 통과
    parts = t.split(maxsplit=1)
    if not parts:
        return None
    head = parts[0].split("@")[0].lower()   # 그룹에서 쓰는 '/토론@봇이름' 대응
    cmd = COMMAND_ALIASES.get(head)
    if not cmd:
        # 슬래시로 시작했다면 오타로 보고 안내를 띄운다
        return ("?", t) if (text or "").strip().startswith("/") else None
    return (cmd, parts[1] if len(parts) > 1 else "")


def fetch_commands(config, state):
    """봇에게 온 새 메시지에서 명령만 골라낸다.

    내 채팅에서 온 것만 처리한다. 봇 주소를 아는 다른 사람이 말을 걸어도
    내 계좌 정보로 무언가 실행되면 안 되기 때문.
    """
    token = config.get("텔레그램_토큰", "")
    my_chat = str(config.get("텔레그램_채팅ID", ""))
    params = {"timeout": 0, "allowed_updates": '["message"]'}
    offset = state.get("tg_offset")
    if offset:
        params["offset"] = offset
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                     params=params, timeout=20)
    data = r.json()
    if not data.get("ok"):
        log(f"명령 조회 실패: {str(data)[:150]}")
        return []
    cmds, last = [], offset
    for u in data.get("result", []):
        last = u["update_id"] + 1
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != my_chat:
            log(f"  다른 사람({chat_id})의 메시지는 무시합니다")
            continue
        norm = normalize_command(text)
        if norm:
            cmds.append(norm)
        elif text:
            log(f"  명령이 아닌 메시지는 건너뜁니다: {text[:30]}")
    if last:
        state["tg_offset"] = last     # 같은 명령을 두 번 실행하지 않도록 표시
    return cmds


# 티커로 인정할 형태만 통과시킨다 (영문 1~5자 또는 숫자 6자리).
# 명령 문자열이 그대로 다른 곳에 흘러가지 않게 하는 안전장치.
TICKER_RE = re.compile(r"^(?:[A-Z]{1,5}|\d{6})$")


def parse_tickers(arg, config):
    known = {str(t).upper() for t in config.get("종목", [])}
    out = []
    for raw in re.split(r"[,\s]+", arg.upper()):
        raw = raw.strip()
        if raw and TICKER_RE.match(raw) and raw not in out:
            out.append(raw)
    unknown = [t for t in out if t not in known]
    return out, unknown


def run_commands(config, state):
    """텔레그램으로 받은 명령을 실행 (GitHub Actions가 5분마다 호출)"""
    try:
        cmds = fetch_commands(config, state)
    except requests.RequestException as e:
        log(f"명령 조회 실패: {type(e).__name__}")
        return False
    if not cmds:
        log("새 명령 없음")
        save_state(state)
        return False

    # 같은 명령이 여러 번 쌓여 있어도 한 번만 실행한다.
    # (오타로 다시 보내거나 답이 없어 또 보내는 일이 잦은데, 브리핑은
    #  1~2분에 API 비용까지 드므로 그대로 두면 그만큼 중복 청구된다)
    uniq, seen = [], set()
    for item in cmds:
        if item in seen:
            log(f"  같은 명령이 중복돼 건너뜁니다: {item[0]}")
            continue
        seen.add(item)
        uniq.append(item)
    cmds = uniq

    did_work = False
    for cmd, arg in cmds:
        log(f"명령 수신: {cmd} {arg}".strip())

        if cmd == "도움말":
            send_telegram(config, HELP_TEXT)

        elif cmd == "새로고침":
            refresh_prices(config, state)
            m = {x["code"]: x for x in state.get("macro", [])}
            vix = m.get(".VIX")
            extra = f"\n공포지수 {vix['value']:.1f} {vix.get('mood','')}" if vix else ""
            send_telegram(config, "🔄 <b>대시보드를 갱신했어요</b>" + extra)
            did_work = True

        elif cmd == "토론":
            tickers, unknown = parse_tickers(arg, config)
            if not tickers:
                send_telegram(config, "종목을 알려주세요. 예) <code>/토론 NVDA</code>")
                continue
            if unknown:
                send_telegram(config, "감시 목록에 없는 종목이에요: "
                                      + esc(", ".join(unknown))
                              + "\n<code>/종목추가 " + esc(unknown[0]) + "</code> 로 먼저 넣어주세요.")
                continue
            send_telegram(config, f"🥊 <b>{esc(', '.join(tickers))}</b> 분석을 시작할게요. 30초쯤 걸려요.")
            results = run_debates(config, load_team(), load_feed(), tickers=tickers)
            merged = [d for d in load_debates() if d["ticker"] not in
                      {r["ticker"] for r in results}]
            save_feed(load_feed(), load_feed_stocks(), debates=(results + merged)[:8])
            for d in results:
                send_telegram(config, format_verdict(d))
            did_work = True

        elif cmd == "종목":
            cur = [str(t) for t in config.get("종목", [])]
            lines = [f"· {esc(state['names'].get(t, t))} ({esc(t)})" for t in cur]
            send_telegram(config, f"📋 <b>감시 중인 종목 {len(cur)}개</b>\n"
                          + "\n".join(lines)
                          + "\n\n추가: <code>/종목추가 PLTR</code>")

        elif cmd in ("종목추가", "종목삭제"):
            wanted, _ = parse_tickers(arg, config)
            if not wanted:
                send_telegram(config, "종목을 알려주세요. 예) <code>/종목추가 PLTR</code>\n"
                                      "미국은 티커, 한국은 6자리 숫자예요.")
                continue
            cur = [str(t) for t in config.get("종목", [])]
            if cmd == "종목추가":
                added, skipped, bad = [], [], []
                for t in wanted:
                    if t in cur:
                        skipped.append(t)
                        continue
                    if len(cur) + len(added) >= MAX_STOCKS:
                        bad.append(f"{t}(개수 한도 {MAX_STOCKS})")
                        continue
                    try:   # 실제로 있는 종목인지 토스에서 확인하고 넣는다
                        _, name, _, _ = lookup_stock(t)
                        added.append(t)
                        state["names"][t] = name
                    except (requests.RequestException, KeyError, TypeError, ValueError):
                        bad.append(f"{t}(못 찾음)")
                cur += added
                msg = []
                if added:
                    msg.append("➕ 추가: " + esc(", ".join(
                        f"{state['names'].get(t, t)}({t})" for t in added)))
                    msg.append("다음 확인부터 분석해요. 기존 뉴스는 건너뛰니 알림 폭탄은 없어요.")
                if skipped:
                    msg.append("이미 보고 있어요: " + esc(", ".join(skipped)))
                if bad:
                    msg.append("⚠ 넣지 못함: " + esc(", ".join(bad)))
                if added:
                    save_stocks(cur)
                send_telegram(config, "\n".join(msg))
            else:
                gone = [t for t in wanted if t in cur]
                cur = [t for t in cur if t not in wanted]
                if gone:
                    save_stocks(cur)
                    send_telegram(config, "➖ 뺐어요: " + esc(", ".join(gone))
                                  + f"\n남은 종목 {len(cur)}개")
                else:
                    send_telegram(config, "그 종목은 목록에 없어요: " + esc(", ".join(wanted)))
            config["종목"] = cur
            did_work = True

        elif cmd == "브리핑":
            send_telegram(config, "🌅 브리핑을 만들고 있어요. 1~2분 걸려요.")
            daily_run(config, state, force=True)
            did_work = True

        else:
            send_telegram(config, f"모르는 명령이에요: {esc(arg)}\n\n" + HELP_TEXT)

    save_state(state)
    return did_work


def get_chat_id(config):
    token = config.get("텔레그램_토큰", "")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    data = r.json()
    if not data.get("ok"):
        print("봇 토큰이 잘못된 것 같습니다:", data)
        return
    chats = {}
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            name = chat.get("first_name") or chat.get("title") or ""
            chats[chat["id"]] = name
    if not chats:
        print("아직 봇이 받은 메시지가 없습니다.")
        print("텔레그램에서 봇에게 아무 메시지나 먼저 보낸 뒤 다시 실행하세요.")
        return
    print("발견된 채팅 ID:")
    for cid, name in chats.items():
        print(f"  {cid}  ({name})")
    print("이 숫자를 config.json의 '텔레그램_채팅ID'에 넣으세요.")


# ---------------- 메인 로직 ----------------

def format_alert(stock_name, ticker, page_code, title, source, created_at, analysis):
    emoji = VERDICT_EMOJI.get(analysis["판정"], "⚪")
    stars = "★" * analysis["중요도"] + "☆" * (5 - analysis["중요도"])
    when = created_at.replace("T", " ")[:16] if created_at else ""
    link = f"https://www.tossinvest.com/stocks/{page_code}/news"
    return (
        f"{emoji} <b>{analysis['판정']}</b> · {esc(stock_name)}({esc(ticker)}) · {stars}\n"
        f"<b>{esc(title)}</b>\n"
        f"💡 {esc(analysis['이유'])}\n"
        f"📰 {esc(source)} · {when}\n"
        f'<a href="{link}">토스증권에서 보기</a>'
    )


def check_ticker(config, state, ticker, feed=None):
    """한 종목의 새 뉴스를 확인하고 알림 전송. 보낸 알림 수 반환."""
    # 종목 코드 조회 (state에 캐시)
    if ticker not in state["codes"]:
        try:
            company_code, name, page_code, isin = lookup_stock(ticker)
        except (requests.RequestException, KeyError) as e:
            log(f"[{ticker}] 종목 조회 실패: {e}")
            return 0
        state["codes"][ticker] = {"company": company_code, "page": page_code, "isin": isin}
        state["names"][ticker] = name
    company_code = state["codes"][ticker]["company"]
    page_code = state["codes"][ticker]["page"]
    stock_name = state["names"].get(ticker, ticker)

    try:
        news_list = fetch_news(company_code, size=int(config.get("뉴스조회_개수", 30)))
    except (requests.RequestException, KeyError) as e:
        log(f"[{stock_name}] 뉴스 조회 실패: {e}")
        return 0

    seen = state["seen"].get(ticker)
    first_run = seen is None
    if first_run:
        # 처음 등록된 종목은 기존 뉴스를 전부 '본 것'으로 처리 (알림 폭탄 방지)
        state["seen"][ticker] = [n["id"] for n in news_list]
        log(f"[{stock_name}] 첫 등록 — 기존 뉴스 {len(news_list)}건은 건너뛰고 이후 새 뉴스부터 알림")
        return 0

    seen_set = set(seen)
    new_items = [n for n in news_list if n["id"] not in seen_set]
    if not new_items:
        log(f"[{stock_name}] 새 뉴스 없음")
        return 0

    max_per_cycle = int(config.get("사이클당_최대분석", 10))
    new_items = list(reversed(new_items))[-max_per_cycle:]  # 오래된 것부터, 과다 분석 방지
    log(f"[{stock_name}] 새 뉴스 {len(new_items)}건 분석 시작")

    min_importance = int(config.get("최소중요도", 3))
    alert_neutral = bool(config.get("중립알림", False))
    sent = 0
    for n in new_items:
        title = n.get("title") or ""
        content = n.get("contentText") or n.get("summary") or ""
        source = (n.get("source") or {}).get("name", "")
        analysis = analyze_news(config, stock_name, ticker, title, content)
        seen.append(n["id"])
        if analysis is None:
            log(f"  · {title[:40]} → 분석 실패, 건너뜀")
            continue
        log(f"  · {title[:40]} → {analysis['판정']} (중요도 {analysis['중요도']}) {analysis['이유'][:50]}")
        skip = (analysis["판정"] == "중립" and not alert_neutral) or \
               (analysis["중요도"] < min_importance)
        alerted = False
        if not skip:
            msg = format_alert(stock_name, ticker, page_code, title, source,
                               n.get("createdAt", ""), analysis)
            if send_telegram(config, msg):
                sent += 1
                alerted = True
            time.sleep(0.5)
        if feed is not None:
            feed.insert(0, {
                "id": n["id"], "ticker": ticker, "name": stock_name,
                "verdict": analysis["판정"], "importance": analysis["중요도"],
                "reason": analysis["이유"], "title": title, "source": source,
                "createdAt": n.get("createdAt", ""),
                "analyzedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "alerted": alerted,
            })

    state["seen"][ticker] = seen[-200:]  # 오래된 기록은 버림
    return sent


def run_cycle(config, state):
    total = 0
    feed = load_feed()
    before = len(feed)
    for ticker in config.get("종목", []):
        total += check_ticker(config, state, str(ticker), feed)
        save_state(state)
    new_items = feed[:len(feed) - before]  # 이번 사이클에 분석된 뉴스
    # 대시보드용 시세 + 피드 저장
    stocks = []
    try:
        tickers = [str(t) for t in config.get("종목", []) if str(t) in state["codes"]]
        prices = fetch_prices([state["codes"][t]["page"] for t in tickers])
        for t in tickers:
            close, chg = prices.get(state["codes"][t]["page"], (None, None))
            stocks.append({"ticker": t, "name": state["names"].get(t, t),
                           "price": close, "change": chg})
    except (requests.RequestException, KeyError) as e:
        log(f"시세 조회 실패(대시보드에만 영향): {e}")
    macro = fetch_macro()
    state["macro"] = macro
    log("타로·디아나·바이브 종목 진단 시작")
    team, candle_map = build_team(config, state, feed)
    graded = grade_feed(feed, candle_map)
    if graded:
        log(f"과거 판정 {len(graded)}건 채점 완료")
    debates = run_debates(config, team, feed)
    save_state(state)
    save_feed(feed, stocks, team, debates, macro)
    return {"sent": total, "new_items": new_items, "stocks": stocks,
            "team": team, "feed": feed, "candle_map": candle_map,
            "graded": graded, "debates": debates, "macro": macro}


def hit_stats(feed, days=30):
    """최근 N일 채점 결과 → {'호재':(적중,전체), '악재':(적중,전체)} (1일 기준)"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    stats = {"호재": [0, 0], "악재": [0, 0]}
    for n in feed:
        if n.get("verdict") in stats and "hit1" in n and (n.get("createdAt") or "") >= cutoff:
            stats[n["verdict"]][1] += 1
            if n["hit1"]:
                stats[n["verdict"]][0] += 1
    return stats


def esc(s):
    """텔레그램 HTML 파싱용 이스케이프"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(s, n):
    """n자 이내로 자르되 단어 중간이 잘리면 앞 공백까지만"""
    s = str(s).strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,·…") + "…"


def build_briefing(state, result, earnings):
    """아침 브리핑 — 한눈에 들어오게 섹션 5개로 압축"""
    WD = ["월", "화", "수", "목", "금", "토", "일"]
    now = datetime.now()
    nm = lambda t: state["names"].get(t, t)      # 티커 → 종목명
    P = []

    P.append(f"🌅 <b>아침 브리핑</b>   {now.month}/{now.day}({WD[now.weekday()]})")

    # ── 0. 시장 풍향계 (개별 종목보다 먼저 전체 분위기) ──
    macro = {m["code"]: m for m in (result.get("macro") or [])}
    if macro:
        def mv(code, fmt="{:,.0f}"):
            m = macro.get(code)
            if not m:
                return None
            c = m.get("change")
            return f"{fmt.format(m['value'])}({c:+.1f}%)" if c is not None else fmt.format(m["value"])
        vix = macro.get(".VIX")
        line = []
        if vix:
            face = {"잠잠함": "😌", "보통": "🙂", "불안": "😟", "공포": "😱"}.get(vix.get("mood"), "")
            line.append(f"{face} 공포지수 {vix['value']:.1f} <b>{vix.get('mood','')}</b>")
        idx = [f"{lbl} {mv(c, '{:,.0f}')}" for c, lbl in
               ((".IXIC", "나스닥"), ("KOSPI", "코스피")) if mv(c)]
        fx = mv("FX_USDKRW", "{:,.0f}원")
        if idx:
            line.append(" · ".join(idx))
        if fx:
            line.append(f"환율 {fx}")
        if line:
            P.append("\n<b>🌡 시장 분위기</b>")
            P.append("\n".join(line))

    # ── 1. 밤사이 시장 ──
    movers = [s for s in result["stocks"] if s.get("change") is not None]
    movers.sort(key=lambda s: -abs(s["change"]))
    big = [s for s in movers if abs(s["change"]) >= 2][:4]
    P.append("\n<b>📉 밤사이</b>")
    if big:
        for s in big:
            arrow = "▲" if s["change"] > 0 else "▼"
            P.append(f"{arrow} {esc(nm(s['ticker']))} <b>{s['change']:+.1f}%</b>")
    else:
        P.append("큰 변동 없음 (±2% 이내)")

    # ── 2. 점수 하이라이트 (강세 / 약세만) ──
    scored = sorted([t for t in result["team"] if t.get("점수")],
                    key=lambda t: -t["점수"]["종합"])
    if scored:
        strong = [t for t in scored if t.get("신호") == "강세"][:2]
        weak = [t for t in scored if t.get("신호") == "약세"][-2:]
        P.append("\n<b>🚦 오늘의 신호</b>")
        if strong:
            for t in strong:
                P.append(f"🟢 <b>{esc(nm(t['ticker']))}</b> {t['점수']['종합']}점\n     {esc(clip(t.get('총평', ''), 34))}")
        if weak:
            for t in reversed(weak):
                P.append(f"🔴 <b>{esc(nm(t['ticker']))}</b> {t['점수']['종합']}점\n     {esc(clip(t.get('총평', ''), 34))}")
        if not strong and not weak:
            P.append(f"전 종목 중립 (최고 {esc(nm(scored[0]['ticker']))} {scored[0]['점수']['종합']}점)")

    # ── 3. 중요 뉴스 (중요도 4+ 우선, 최대 3건) ──
    items = [n for n in result["new_items"] if n["verdict"] != "중립"]
    top, seen_tk = [], {}
    for n in sorted(items, key=lambda n: -n["importance"]):
        if seen_tk.get(n["ticker"], 0) >= 1:   # 한 종목당 1건까지만
            continue
        seen_tk[n["ticker"]] = seen_tk.get(n["ticker"], 0) + 1
        top.append(n)
        if len(top) == 3:
            break
    if top:
        P.append("\n<b>📰 중요 뉴스</b>")
        for n in top:
            emoji = "🟢" if n["verdict"] == "호재" else "🔴"
            P.append(f"{emoji} {esc(nm(n['ticker']))} · {esc(clip(n['title'], 34))}")
    elif result["new_items"]:
        P.append(f"\n<b>📰 중요 뉴스</b>\n없음 (중립 {len(result['new_items'])}건만)")

    # ── 3.5 오늘의 토론 ──
    for d in (result.get("debates") or [])[:2]:
        P.append(f"\n<b>🥊 오늘의 토론 · {esc(d['name'])}</b>")
        P.append(f"{CALL_EMOJI.get(d.get('판단'), '⚖️')} <b>{esc(d.get('판단','홀드'))}</b>"
                 f" (확신 {esc(d.get('확신도','보통'))})")
        if d.get("한줄"):
            P.append(esc(clip(d["한줄"], 52)))
        if d.get("무효화조건"):
            P.append(f"↩︎ 뒤집힐 때: {esc(clip(d['무효화조건'], 46))}")

    # ── 4. 실적 발표 (3일 이내만) ──
    from zoneinfo import ZoneInfo
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    soon = {}
    for s, d in earnings.items():
        if s.startswith("_"):
            continue
        try:
            gap = (datetime.strptime(d, "%Y-%m-%d").date() - today_ny).days
        except ValueError:
            continue
        if 0 <= gap <= 3:
            soon.setdefault(gap, []).append(nm(s))
    if soon:
        label = {0: "오늘", 1: "내일", 2: "모레", 3: "3일 뒤"}
        P.append("\n<b>📅 실적 발표</b>")
        for gap in sorted(soon):
            P.append(f"{label[gap]} · {esc(', '.join(soon[gap][:4]))}")

    # ── 5. 적중률 (숫자만) ──
    graded = result.get("graded", [])
    st = hit_stats(result["feed"])
    tot_h = sum(h for h, _ in st.values())
    tot_n = sum(n for _, n in st.values())
    if graded or tot_n >= 3:
        P.append("\n<b>🎯 판정 성적</b>")
        if graded:
            hits = sum(1 for n in graded if n["hit1"])
            P.append(f"어제 {hits}/{len(graded)} 적중")
        if tot_n >= 3:
            P.append(f"30일 누적 <b>{round(tot_h / tot_n * 100)}%</b> ({tot_h}/{tot_n})")

    P.append("\n<i>자세한 내용은 대시보드에서 →</i>")
    return "\n".join(P)[:4000]


def send_earnings_alerts(config, state, earnings):
    """내일 실적 발표 종목 알림 (하루 한 번)"""
    from zoneinfo import ZoneInfo
    tomorrow = (datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=1)).isoformat()
    alerted = state.setdefault("earnings_alerted", {})
    targets = [s for s, d in earnings.items()
               if not s.startswith("_") and d == tomorrow and alerted.get(s) != d]
    if not targets:
        return 0
    names = " · ".join(f"{state['names'].get(t, t)}({t})" for t in targets)
    if send_telegram(config, f"📅 <b>내일 실적 발표</b>\n{names}\n변동성이 커질 수 있어요!"):
        for t in targets:
            alerted[t] = tomorrow
        return len(targets)
    return 0


def price_watch(config, state):
    """급등락 감시: 전일 대비 기준선(기본 5%)을 넘을 때마다 즉시 알림"""
    from zoneinfo import ZoneInfo
    threshold = float(config.get("급등락_기준퍼센트", 5))
    ny_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    levels = state.setdefault("price_levels", {})
    tickers = [str(t) for t in config.get("종목", []) if str(t) in state["codes"]]
    if not tickers:
        log("급등락 감시: 등록된 종목 없음 (뉴스 확인을 먼저 한 번 실행하세요)")
        return
    prices = refresh_prices(config, state)  # 대시보드 시세도 함께 갱신
    if prices is None:
        return
    alerts = []
    for t in tickers:
        close, chg = prices.get(state["codes"][t]["page"], (None, None))
        if chg is None:
            continue
        level = int(abs(chg) // threshold)
        prev = levels.get(t, {})
        prev_level = prev.get("level", 0) if prev.get("date") == ny_date else 0
        if level >= 1 and level > prev_level:
            arrow = "🚀" if chg > 0 else "📉"
            alerts.append(f"{arrow} {state['names'].get(t, t)}({t}) <b>{'+' if chg > 0 else ''}{chg}%</b> (${close})")
            levels[t] = {"date": ny_date, "level": level}
    if alerts:
        send_telegram(config, "⚡ <b>급등락 감지</b> (전일 대비)\n" + "\n".join(alerts))
        log(f"급등락 알림 {len(alerts)}건 전송")
    else:
        log("급등락 없음")
    save_state(state)


def weekly_report(config, state):
    """주간 성적표: 주간 등락 + 뉴스 통계 + 적중률 복기"""
    feed = load_feed()
    tickers = [str(t) for t in config.get("종목", []) if str(t) in state["codes"]]
    weekly = []
    for t in tickers:
        try:
            _, closes, _, _, _ = fetch_candles(state["codes"][t]["page"], count=10)
            if len(closes) >= 6:
                weekly.append((t, round((closes[-1] / closes[-6] - 1) * 100, 1)))
        except (requests.RequestException, KeyError):
            continue
    weekly.sort(key=lambda x: -x[1])
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    wk = [n for n in feed if (n.get("analyzedAt") or "") >= cutoff]
    good = sum(1 for n in wk if n["verdict"] == "호재")
    bad = sum(1 for n in wk if n["verdict"] == "악재")

    parts = [f"📊 <b>주간 성적표</b> ({(datetime.now() - timedelta(days=6)).strftime('%m/%d')}~{datetime.now().strftime('%m/%d')})"]
    if weekly:
        best = " · ".join(f"{t} {'+' if c > 0 else ''}{c}%" for t, c in weekly[:3])
        worst = " · ".join(f"{t} {'+' if c > 0 else ''}{c}%" for t, c in weekly[-3:][::-1])
        parts.append(f"🏆 베스트: {best}")
        parts.append(f"🧊 워스트: {worst}")
    parts.append(f"📰 이번 주 뉴스 {len(wk)}건 (🟢{good} 🔴{bad} ⚪{len(wk) - good - bad})")

    st1 = hit_stats(feed, days=7)
    seg = [f"{v} {round(h / n * 100)}%({h}/{n})" for v, (h, n) in st1.items() if n >= 1]
    if seg:
        parts.append("🎯 이번 주 판정 적중(다음날 기준): " + " · ".join(seg))
    wk_graded = [n for n in feed if "hit1" in n and (n.get("createdAt") or "") >= cutoff]
    best = sorted([n for n in wk_graded if n["hit1"]], key=lambda n: -abs(n["chk1"]))[:2]
    worst = sorted([n for n in wk_graded if not n["hit1"]], key=lambda n: -abs(n["chk1"]))[:2]
    for n in best:
        parts.append(f" ✅ [{n['ticker']}] {n['verdict']} → {'+' if n['chk1'] > 0 else ''}{n['chk1']}% · {n['title'][:28]}")
    for n in worst:
        parts.append(f" ❌ [{n['ticker']}] {n['verdict']} → {'+' if n['chk1'] > 0 else ''}{n['chk1']}% · {n['title'][:28]}")
    st30 = hit_stats(feed, days=30)
    seg = [f"{v} {round(h / n * 100)}%({h}/{n})" for v, (h, n) in st30.items() if n >= 3]
    if seg:
        parts.append("🎯 최근 30일 누적: " + " · ".join(seg))

    earnings = state.get("earnings", {})
    upcoming = sorted((d, s) for s, d in earnings.items() if not s.startswith("_"))
    if upcoming:
        parts.append("📅 다가오는 실적: " + " · ".join(f"{d[5:].replace('-', '/')} {s}" for d, s in upcoming[:6]))
    parts.append("한 주도 수고했어요! 🎮")
    return send_telegram(config, "\n".join(parts)[:4000])


def last_us_close():
    """가장 최근에 지나간 뉴욕 16:00의 유닉스 시각"""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < close:
        close -= timedelta(days=1)
    return close.timestamp(), now


def daily_run(config, state, force=False):
    """아침 브리핑 1회 (스케줄 실행 + 텔레그램 /브리핑 명령 공용)"""
    # 최근 뉴욕 16:00(장 마감) 이후 아직 안 돌았으면만 실행 (중복 방지)
    close_ts, ny_now = last_us_close()
    if float(state.get("last_daily", 0)) >= close_ts and not force:
        log(f"오늘 장 마감분은 이미 확인함 (뉴욕 {ny_now.strftime('%m-%d %H:%M')}) — 종료")
        return False
    log(f"일일 확인 시작 (뉴욕 {ny_now.strftime('%m-%d %H:%M')})")
    # 브리핑 모드: 개별 알림은 초중요 뉴스만, 나머지는 브리핑 1통에 요약
    daily_config = dict(config)
    daily_config["최소중요도"] = int(config.get("즉시알림_최소중요도", 4))
    result = run_cycle(daily_config, state)
    earnings = {}
    try:
        earnings = fetch_earnings(state, config)
        send_earnings_alerts(config, state, earnings)
    except (requests.RequestException, ValueError) as e:
        log(f"실적 캘린더 조회 실패: {e}")
    send_telegram(config, build_briefing(state, result, earnings))
    state["last_daily"] = time.time()
    save_state(state)
    log(f"일일 확인 완료 — 브리핑 1통 + 개별 알림 {result['sent']}건 전송")
    return True


def main():
    config = load_config()

    if "--get-chat-id" in sys.argv:
        get_chat_id(config)
        return
    if "--test" in sys.argv:
        ok = send_telegram(config, "✅ 토스뉴스알리미 테스트 메시지입니다!")
        print("전송 성공!" if ok else "전송 실패 — config.json의 토큰/채팅ID를 확인하세요.")
        return

    # 설정 확인
    problems = []
    if "여기에" in config.get("텔레그램_토큰", "여기에"):
        problems.append("텔레그램_토큰")
    if "여기에" in str(config.get("텔레그램_채팅ID", "여기에")):
        problems.append("텔레그램_채팅ID")
    if "여기에" in config.get("ANTHROPIC_API_KEY", "여기에"):
        problems.append("ANTHROPIC_API_KEY")
    if problems:
        log(f"config.json에서 다음 항목을 먼저 채워주세요: {', '.join(problems)}")
        log("자세한 방법은 설정방법.md 참고")
        sys.exit(1)

    state = load_state()
    interval_min = float(config.get("확인주기_분", 10))

    if "--debate" in sys.argv:
        i = sys.argv.index("--debate")
        arg = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        tickers = [t.strip() for t in arg.replace(",", " ").split() if t.strip()]
        team = load_team()
        if not team:
            log("먼저 분석을 한 번 실행해야 해요: news_alert.py --once")
            return
        if not tickers:
            log("사용법: news_alert.py --debate NVDA     (여러 개는 쉼표로: NVDA,TSLA)")
            log("가능한 종목: " + ", ".join(f"{t['ticker']}({t['name']})" for t in team))
            return
        feed = load_feed()
        new = run_debates(config, team, feed, tickers=tickers)
        if new:
            keep = [d for d in load_debates() if d["ticker"] not in {x["ticker"] for x in new}]
            debates = new + keep
            save_feed(feed, load_feed_stocks(), team, debates[:8])
            for d in new:
                send_telegram(config, format_verdict(d))
            log("대시보드를 새로고침하면 토론 결과가 보여요.")
        return

    if "--refresh" in sys.argv:
        refresh_prices(config, state)
        return

    if "--price-watch" in sys.argv:
        price_watch(config, state)
        return

    if "--weekly" in sys.argv:
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("last_weekly") == today and "--force" not in sys.argv:
            log("오늘 주간 리포트는 이미 보냄 — 종료")
            return
        if weekly_report(config, state):
            state["last_weekly"] = today
            save_state(state)
            log("주간 성적표 전송 완료")
        return

    if "--once" in sys.argv:
        result = run_cycle(config, state)
        log(f"확인 완료 — 알림 {result['sent']}건 전송")
        return

    if "--daily" in sys.argv:
        daily_run(config, state, force="--force" in sys.argv)
        return

    if "--commands" in sys.argv:
        run_commands(config, state)
        return

    # 모르는 옵션을 줬는데 조용히 무한 감시 루프로 빠지지 않도록 검사
    KNOWN = {"--once", "--daily", "--weekly", "--refresh", "--price-watch",
             "--debate", "--commands", "--test", "--get-chat-id", "--force"}
    unknown = [a for a in sys.argv[1:] if a.startswith("-") and a not in KNOWN]
    if unknown:
        log(f"모르는 옵션입니다: {' '.join(unknown)}")
        log("사용 가능: " + " ".join(sorted(KNOWN)))
        sys.exit(1)

    log(f"토스뉴스알리미 시작 — 종목 {len(config.get('종목', []))}개, {interval_min}분마다 확인")
    while True:
        try:
            result = run_cycle(config, state)
            if result["sent"]:
                log(f"이번 사이클 알림 {result['sent']}건 전송")
        except Exception as e:  # 루프는 어떤 오류에도 죽지 않게
            log(f"사이클 오류: {e}")
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    try:
        main()
    except requests.RequestException as e:
        # 토스·네이버·텔레그램 API가 잠깐 불안정한 경우가 있다.
        # 자동 실행(클라우드)에서 이런 일시적 오류로 '실패' 알림이 오지 않도록
        # 경고만 남기고 정상 종료한다 — 다음 예약 실행에서 다시 시도된다.
        log(f"⚠ 네트워크 문제로 이번 실행을 건너뜁니다: {type(e).__name__}: {e}")
        log("  다음 예약 실행에서 자동으로 다시 시도합니다.")
        sys.exit(0)
    except KeyboardInterrupt:
        log("사용자가 중단했습니다.")
        sys.exit(0)
