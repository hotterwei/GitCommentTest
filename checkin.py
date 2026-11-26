#!/usr/bin/env python3
"""WorkBuddy 每日签到（云端版）。

只从环境变量读取令牌，不读取本机登录态文件，不依赖 WorkBuddy 客户端。
令牌通过 GitHub Actions Secrets 注入，脚本本身不包含任何凭据。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_ENDPOINT = "https://copilot.tencent.com"
STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CLAIM_PATH = "/v2/billing/meter/daily-checkin"
TIMEOUT = 20


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def credentials() -> tuple[str, str, str]:
    token = os.environ.get("WORKBUDDY_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 WORKBUDDY_ACCESS_TOKEN 环境变量")
    uid = os.environ.get("WORKBUDDY_UID", "").strip()
    endpoint = os.environ.get("WORKBUDDY_ENDPOINT", DEFAULT_ENDPOINT).strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("WORKBUDDY_ENDPOINT 必须是 https:// 开头的官方地址")
    return token, uid, endpoint


def post(path: str, token: str, uid: str, endpoint: str) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "WorkBuddy-Cloud-Checkin/1.0",
    }
    if uid:
        headers["X-User-Id"] = uid
    request = urllib.request.Request(
        endpoint + path,
        data=b"{}",
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, _json(raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        return error.code, _json(raw)
    except Exception as error:  # 网络错误等
        log(f"请求失败：{type(error).__name__}")
        return 0, {}


def _json(raw: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def message_of(body: dict) -> str:
    return str(body.get("message") or body.get("msg") or "")


def already_checked_in(body: dict) -> bool:
    if body.get("code") == 10001:
        return True
    msg = message_of(body)
    return "已签到" in msg or "已经签到" in msg


def report(status: str, extra: dict | None = None) -> dict:
    out = {"date": datetime.now().strftime("%Y-%m-%d"), "status": status}
    if extra:
        out.update(extra)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    try:
        token, uid, endpoint = credentials()
    except RuntimeError as error:
        log(str(error))
        report("FAILED", {"reason": str(error)})
        return 1

    status_code, status_body = post(STATUS_PATH, token, uid, endpoint)
    if status_code in (401, 403):
        reason = "登录令牌已失效，请重新登录客户端并更新 Secrets"
        log(reason)
        report("FAILED", {"reason": reason})
        return 1

    data = status_body.get("data") if isinstance(status_body, dict) else None
    if isinstance(data, dict) and data.get("today_checked_in"):
        report(
            "ALREADY",
            {
                "streak_days": data.get("streak_days"),
                "today_credit": data.get("today_credit"),
                "total_credits": data.get("total_credits"),
            },
        )
        return 0

    if already_checked_in(status_body):
        report("ALREADY")
        return 0

    claim_code, claim_body = post(CLAIM_PATH, token, uid, endpoint)
    if claim_code in (401, 403):
        reason = "登录令牌已失效，请重新登录客户端并更新 Secrets"
        log(reason)
        report("FAILED", {"reason": reason})
        return 1

    if claim_code == 0:
        report("FAILED", {"reason": "网络请求失败"})
        return 1

    if 200 <= claim_code < 300 or (claim_code == 400 and already_checked_in(claim_body)):
        if already_checked_in(claim_body):
            report("ALREADY")
            return 0
        report(
            "SUCCESS",
            {
                "credit": claim_body.get("credit") or claim_body.get("today_credit"),
                "message": message_of(claim_body),
            },
        )
        return 0

    reason = message_of(claim_body) or f"接口返回 HTTP {claim_code}"
    log(reason)
    report("FAILED", {"reason": reason, "http": claim_code})
    return 1


if __name__ == "__main__":
    sys.exit(main())
