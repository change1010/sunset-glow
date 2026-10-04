#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晚霞预报推送 —— 只在高分当天提醒，避免无效打扰。

数据源：
  - glowsunset.cn 现成模型（评分/点评/蓝调/机位/摄影参数）
  - Open-Meteo 三套数值模式（EC/CMA/GFS）云量一致性交叉校验

推送渠道：微信（PushPlus 或 Server酱）

用法：
  python sunset_glow.py --mode digest   # 每 6 小时的常规检查（仅在达到高分线时推送）
  python sunset_glow.py --mode alert    # 日落前的加推检查（仅在日落前窗口内且高分时推送）
  python sunset_glow.py --mode test     # 立刻发一条真实数据的测试推送，忽略去重和窗口限制
  附加参数：--force 忽略已推送去重；--dry-run 只打印不发送

token 取用顺序：环境变量 PUSHPLUS_TOKEN → local_secrets.json → config.json
部署在服务器上时用环境变量注入最省事，本机手动运行用 local_secrets.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOG_DIR = BASE_DIR / "logs"

GLOWSUNSET_SPOT_API = "https://glowsunset.cn/api/spot/{spot}"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SunsetGlowNotifier/1.0"

# 与 glowsunset 前端 GRADE_MAP 一致的官方分级
GRADE_TIERS = [
    (85, "绝美"),
    (60, "很棒"),
    (30, "不错"),
    (1, "平淡"),
    (0, "无望"),
]

STATE_RETAIN_DAYS = 30


def grade_of(quality: int) -> str:
    for floor, label in GRADE_TIERS:
        if quality >= floor:
            return label
    return GRADE_TIERS[-1][1]


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

def setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    try:
        if sys.stdout is not None:  # pythonw 无控制台
            print(line)
    except (OSError, ValueError):
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "sunset_glow.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def fetch_json(url: str, timeout: int = 25, attempts: int = 3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
            last_error = exc
            log(f"请求失败({attempt}/{attempts}) {url} -> {exc}")
    log(f"放弃请求 {url}：{last_error}")
    return None


def http_post(url: str, body: bytes, content_type: str, timeout: int = 20):
    request = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": content_type,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"__error__ {exc}"


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------

def parse_hhmm(day: str, value: str) -> datetime | None:
    try:
        return datetime.strptime(f"{day} {value}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def fetch_forecast(spot: str) -> dict | None:
    return fetch_json(GLOWSUNSET_SPOT_API.format(spot=spot))


def extract_days(payload: dict) -> list[dict]:
    """把接口返回的 forecast 数组整理成统一结构。

    注意：细节字段（云量/机位/摄影参数/建议到达时间）只在顶层对象里，且只对应
    payload['date'] 那一天；forecast 数组里的项只有评分/概率/点评等摘要字段。
    """
    top_date = payload.get("date")
    top_window = payload.get("sunsetWindow") or {}
    top_node = top_window.get("recommendedNode") or {}
    top_node_metrics = top_node.get("metrics") or {}
    top_metrics = payload.get("metrics") or {}
    top_blue = payload.get("blueHour") or {}
    today_metrics = {**{k: top_metrics.get(k) for k in
                        ("cloudHigh", "cloudMid", "cloudLow", "visibilityKm", "windowTransparency")},
                     **{k: top_node_metrics.get(k) for k in
                        ("cloudHigh", "cloudMid", "cloudLow", "visibilityKm", "windowTransparency")
                        if top_node_metrics.get(k) is not None}}
    today_blue = {
        "start": top_blue.get("start"),
        "end": top_blue.get("end"),
        "score": top_blue.get("score"),
        "label": top_blue.get("label"),
        "advice": top_blue.get("advice"),
        "camera": top_blue.get("camera"),
    }

    days = []
    for raw in payload.get("forecast") or []:
        date = raw.get("date")
        if not date:
            continue
        is_today = date == top_date
        quality = raw.get("quality")
        sun = raw.get("sunTimes") or (payload.get("sunTimes") if is_today else None) or {}
        blue = raw.get("blueHour") or (today_blue if is_today else {}) or {}
        days.append({
            "date": date,
            "is_today": is_today,
            "quality": int(quality) if isinstance(quality, (int, float)) else None,
            "grade": grade_of(int(quality)) if isinstance(quality, (int, float)) else "无数据",
            "verdict": raw.get("verdict") or "",
            "probability": raw.get("probability"),
            "probabilityLabel": raw.get("probabilityLabel") or "",
            "sunset": sun.get("sunset"),
            "blueHour": {
                "start": blue.get("start"),
                "end": blue.get("end"),
                "score": blue.get("score"),
                "label": blue.get("label"),
                "advice": blue.get("advice"),
                "camera": blue.get("camera"),
            },
            "arrival": top_window.get("recommendedArrival") if is_today else None,
            "leave": top_window.get("recommendedLeave") if is_today else None,
            "arrivalNote": top_window.get("arrivalNote") if is_today else None,
            "metrics": today_metrics if is_today else {},
            "weather": (raw.get("weather") or {}).get("label"),
            "confidence": payload.get("confidence") if is_today else None,
            "bestSpot": payload.get("bestSpot") if is_today else None,
            "photographyAdvice": payload.get("photographyAdvice") if is_today else None,
            "sunsetWindowMessage": top_window.get("message") if is_today else None,
        })
    return days


def fetch_crosscheck(lat: float, lon: float, target_date: str, sunset: str, models: list[str]) -> dict | None:
    """用 Open-Meteo 的三套数值模式做云量一致性校验。"""
    url = (
        f"{OPEN_METEO_API}?latitude={lat}&longitude={lon}"
        "&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high"
        f"&models={','.join(models)}"
        "&timezone=Asia%2FShanghai&forecast_days=3"
    )
    payload = fetch_json(url, timeout=20, attempts=2)
    if not payload:
        return None
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    hour_key = f"{target_date}T{sunset[:2]}:00" if sunset else None
    # 日落前 1 小时到日落当刻更贴近"画布"状态
    candidates = []
    if hour_key:
        candidates = [hour_key, f"{target_date}T{max(0, int(sunset[:2]) - 1):02d}:00"]
    index = next((times.index(c) for c in candidates if c in times), None)
    if index is None:
        return None

    result = {}
    for model in models:
        entry = {}
        for layer in ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"):
            values = hourly.get(f"{layer}_{model}")
            if values and index < len(values):
                entry[layer.replace("cloud_cover_", "")] = values[index]
        if entry:
            result[model] = entry
    return result or None


def describe_consistency(crosscheck: dict | None, spread_limit: int) -> str:
    if not crosscheck:
        return "模型校验：本次未取到"
    lows = [v.get("low") for v in crosscheck.values() if v.get("low") is not None]
    mids = [v.get("mid") for v in crosscheck.values() if v.get("mid") is not None]
    if not lows:
        return "模型校验：无低云数据"
    short = {"ecmwf_ifs025": "EC", "gfs_seamless": "GFS", "cma_grapes_global": "CMA"}
    parts = " / ".join(
        f"{short.get(m, m)} {crosscheck[m].get('low')}%"
        for m in crosscheck if crosscheck[m].get("low") is not None
    )
    spread = max(lows) - min(lows)
    if spread >= spread_limit:
        return f"⚠️ 模型分歧：低云 {parts}（相差 {spread} 个百分点，结果不确定性高）"
    verdict = "一致" if spread < 25 else "基本一致"
    line = f"模型校验：低云 {parts}（{verdict}）"
    if mids and max(mids) - min(mids) >= spread_limit:
        line += "；中云分歧较大"
    return line


# --------------------------------------------------------------------------
# 文案
# --------------------------------------------------------------------------

def summarize_consistency(crosscheck: dict | None, spread_limit: int) -> str:
    """把多模式一致性压缩成很短的一句，挂在页脚。详细版仍写日志。"""
    if not crosscheck:
        return "Open-Meteo 三模式校验缺失"
    lows = [v.get("low") for v in crosscheck.values() if v.get("low") is not None]
    if not lows:
        return "Open-Meteo 三模式校验缺失"
    spread = max(lows) - min(lows)
    if spread >= spread_limit:
        return "⚠️ Open-Meteo 三模式低云分歧大"
    return "Open-Meteo 三模式低云一致" if spread < 25 else "Open-Meteo 三模式低云基本一致"


def harden(line: str) -> str:
    """行尾补两个空格，强制 markdown 换行（单换行会被渲染成空格而黏成一行）。"""
    return f"{line}  " if line else line


def build_markdown(day: dict, crosscheck_short: str) -> str:
    metrics = day["metrics"]
    blue = day["blueHour"]
    prob = day["probability"]
    prob_text = f"{prob}%（{day['probabilityLabel']}）" if prob is not None else "未知"
    vis = metrics.get("visibilityKm")
    vis_text = f"{vis} km" if vis is not None else "未知"

    lines = [
        f"**{day['date']}** ｜ 日落 **{day['sunset'] or '--'}** ｜ 天气 {day['weather'] or '--'}",
        "",
        f"**综合评分 {day['quality']}/100 — {day['grade']}**",
        f"观赏概率 {prob_text}",
        "",
    ]
    if vis is not None or day["confidence"]:
        lines.append(f"**能见度** {vis_text} ｜ **置信度** {day['confidence'] or '--'}")
    plan = []
    if day["arrival"]:
        plan.append(f"建议 {day['arrival']} 到达")
    if day["leave"]:
        plan.append(f"{day['leave']} 后可撤")
    if plan:
        lines.append(f"**出动** {'，'.join(plan)}")
    if blue.get("start"):
        lines.append(
            f"**蓝调** {blue['start']}–{blue.get('end', '--')}（{blue.get('label') or '--'}"
            + (f"，{blue['score']} 分" if blue.get("score") is not None else "")
            + "）"
        )
    lines += ["", f"<sub>数据：glowsunset.cn · {crosscheck_short}</sub>"]
    return "\n".join(harden(line) for line in lines)


# --------------------------------------------------------------------------
# 推送通道
# --------------------------------------------------------------------------

def resolve_token(channel: dict) -> str:
    """按优先级取 token：环境变量 → local_secrets.json → config.json。

    - 环境变量（PUSHPLUS_TOKEN / SERVERCHAN_TOKEN）：适合部署在服务器上，
      用环境变量注入，token 不写进任何文件。
    - local_secrets.json：本机手动运行用，已加进 .gitignore。
    - config.json：最后回退（不建议在这里填）。
    """
    provider = (channel.get("provider") or "pushplus").lower()
    env_name = {"pushplus": "PUSHPLUS_TOKEN", "serverchan": "SERVERCHAN_TOKEN"}.get(provider)
    if env_name:
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    local = load_json(BASE_DIR / "local_secrets.json", {})
    if isinstance(local, dict):
        value = (local.get(env_name or "") or "").strip()
        if value:
            return value
    return (channel.get("token") or "").strip()


def send_wechat(config: dict, title: str, markdown: str) -> bool:
    channel = config["channels"]["wechat"]
    provider = (channel.get("provider") or "pushplus").lower()
    token = resolve_token(channel)
    if not token:
        log("未取到 token（环境变量与 config.json 都为空），跳过发送")
        return False

    if provider == "pushplus":
        payload = json.dumps({
            "token": token,
            "title": title,
            "content": markdown,
            "template": "markdown",
        }, ensure_ascii=False).encode("utf-8")
        response = http_post("https://www.pushplus.plus/send", payload, "application/json")
        if response and '"code":200' in response.replace(" ", ""):
            log("微信推送(PushPlus)已发送")
            return True
        log(f"微信推送(PushPlus)失败：{response}")
        return False

    if provider == "serverchan":
        from urllib.parse import urlencode
        payload = urlencode({"title": title, "desp": markdown}).encode("utf-8")
        response = http_post(f"https://sctapi.ftqq.com/{token}.send", payload, "application/x-www-form-urlencoded")
        if response and '"code":0' in response.replace(" ", ""):
            log("微信推送(Server酱)已发送")
            return True
        log(f"微信推送(Server酱)失败：{response}")
        return False

    log(f"未知的微信推送 provider：{provider}")
    return False


def dispatch(config: dict, title: str, markdown: str, dry_run: bool) -> None:
    if dry_run:
        log("[dry-run] 未实际发送")
        return
    wechat = config["channels"].get("wechat") or {}
    if not wechat.get("enabled"):
        log("微信推送未启用，本次不发送")
        return
    # 用 resolve_token 判断，才能识别出环境变量注入的 token
    if not resolve_token(wechat):
        log("微信推送已启用但未取到 token，跳过。"
            "请设置环境变量 PUSHPLUS_TOKEN，或填写 local_secrets.json")
        return
    send_wechat(config, title, markdown)


# --------------------------------------------------------------------------
# 判定逻辑
# --------------------------------------------------------------------------

def in_window(now: datetime, start: str, end: str) -> bool:
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()
    return start_t <= now.time() <= end_t


def prune_state(state: dict) -> dict:
    cutoff = datetime.now() - timedelta(days=STATE_RETAIN_DAYS)
    kept = {}
    for key, stamp in (state.get("pushed") or {}).items():
        try:
            if datetime.fromisoformat(stamp) >= cutoff:
                kept[key] = stamp
        except ValueError:
            continue
    state["pushed"] = kept
    return state


def evaluate(config: dict, days: list[dict], now: datetime, state: dict, mode: str, force: bool):
    """返回 (事件列表, 今日前景)。事件为 (去重键, 类型, 当天数据)。"""
    cfg_threshold = int(config["thresholds"]["push_min_quality"])
    fire_threshold = int(config["thresholds"]["advance_fire_quality"])
    schedule = config["schedule"]
    pushed = state.setdefault("pushed", {})
    today_str = now.strftime("%Y-%m-%d")
    today = next((d for d in days if d["date"] == today_str), None)
    events = []

    def fresh(key: str) -> bool:
        return force or key not in pushed

    if not today or today["quality"] is None:
        return events, today

    sunset_dt = parse_hhmm(today["date"], today["sunset"]) if today["sunset"] else None
    within_push_hours = in_window(now, schedule["push_window_start"], schedule["push_window_end"])

    if mode == "alert":
        if (sunset_dt and today["quality"] >= cfg_threshold and fresh(f"{today_str}:alert")):
            earliest = sunset_dt - timedelta(hours=float(schedule["alert_earliest_hours_before_sunset"]))
            latest = sunset_dt - timedelta(minutes=int(schedule["alert_latest_minutes_before_sunset"]))
            if earliest <= now <= latest and within_push_hours:
                events.append((f"{today_str}:alert", "alert", today))
    else:  # digest
        if today["quality"] >= cfg_threshold and fresh(f"{today_str}:digest"):
            still_useful = sunset_dt and now < sunset_dt - timedelta(
                minutes=int(schedule["stop_before_sunset_minutes"])
            )
            if still_useful and within_push_hours:
                events.append((f"{today_str}:digest", "digest", today))
        if config["advanced"].get("announce_advance_fire_day", True):
            upcoming = next((d for d in days if d["date"] > today_str and d["quality"] is not None), None)
            if (upcoming and upcoming["quality"] >= fire_threshold
                    and fresh(f"{upcoming['date']}:advance") and within_push_hours):
                events.append((f"{upcoming['date']}:advance", "advance", upcoming))
    return events, today


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="晚霞预报推送")
    parser.add_argument("--mode", choices=["digest", "alert", "test"], default="digest")
    parser.add_argument("--force", action="store_true", help="忽略已推送去重")
    parser.add_argument("--dry-run", action="store_true", help="只打印不发送")
    args = parser.parse_args()

    setup_stdout()
    config = load_json(CONFIG_PATH, None)
    if not config:
        log(f"配置读取失败：{CONFIG_PATH}")
        return 1
    state = prune_state(load_json(STATE_PATH, {"pushed": {}}))
    location = config["location"]
    now = datetime.now()

    payload = fetch_forecast(location["spot"])
    if not payload:
        log("未能取到 glowsunset 预测数据，本次退出（不做降级推送，避免误报）")
        save_json(STATE_PATH, state)
        return 0

    days = extract_days(payload)
    if not days:
        log("预测数据为空，本次退出")
        return 0

    today_str = now.strftime("%Y-%m-%d")
    today = next((d for d in days if d["date"] == today_str), days[0])
    log(f"取数成功：{location['name']} {today['date']} 评分 {today['quality']}（{today['grade']}）"
        f" 日落 {today['sunset']} 概率 {today['probability']}")

    crosscheck = fetch_crosscheck(
        location["latitude"], location["longitude"], today["date"], today["sunset"] or "18:00",
        config["advanced"]["crosscheck_models"],
    )
    spread_limit = int(config["advanced"]["crosscheck_disagreement_spread"])
    crosscheck_line = describe_consistency(crosscheck, spread_limit)
    crosscheck_short = summarize_consistency(crosscheck, spread_limit)
    log(crosscheck_line)
    state["last_crosscheck"] = {"date": today["date"], "text": crosscheck_line, "at": now.isoformat(timespec="seconds")}

    if args.mode == "test":
        markdown = build_markdown(today, crosscheck_short)
        title = f"{location['name']}晚霞 {today['quality']}分·{today['grade']}"
        log("---- 即将发送的内容（测试触发，格式与真实推送完全一致）----")
        log(title)
        log(markdown)
        dispatch(config, title, markdown, args.dry_run)
        save_json(STATE_PATH, state)
        return 0

    events, _ = evaluate(config, days, now, state, args.mode, args.force)
    if not events:
        log("未达高分线或今日已提醒过，本次不推送")
        save_json(STATE_PATH, state)
        return 0

    for key, kind, day in events:
        markdown = build_markdown(day, crosscheck_short)
        title = f"{location['name']}晚霞 {day['quality']}分·{day['grade']}"
        log(f"---- 触发推送 {kind} / {day['date']} ----")
        log(title)
        log(markdown)
        dispatch(config, title, markdown, args.dry_run)
        if not args.dry_run:
            state["pushed"][key] = now.isoformat(timespec="seconds")

    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
