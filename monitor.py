import base64
import json
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
QQBOT_STATE_PATH = ROOT / "qqbot_state.json"


def pick(data, *keys, default=None):
    for key in keys:
        if key in data:
            return data[key]
    return default


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(method, url, token="", body=None, extra_headers=None, return_headers=False):
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    if token:
        headers["authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return (data, resp.headers) if return_headers else data


def jwt_expired(token):
    raw = token.removeprefix("Bearer ").strip()
    if not raw or raw.count(".") != 2:
        return True
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return int(data.get("exp", 0)) < int(time.time()) + 300
    except Exception:
        return True


def find_token(data):
    if isinstance(data, dict):
        for key in ("token", "access_token", "jwt"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        for val in data.values():
            found = find_token(val)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = find_token(item)
            if found:
                return found
    return ""


def login(site):
    url = pick(site, "网址", "base_url").rstrip("/") + "/api/v1/auth/login"
    resp = request_json("POST", url, body={
        "email": pick(site, "邮箱", "email"),
        "password": pick(site, "密码", "password"),
    })
    token = find_token(resp)
    if not token:
        raise RuntimeError(f"{pick(site, '名称', 'name')} 登录成功但没找到 token，返回：{resp}")
    return token if token.startswith("Bearer ") else f"Bearer {token}"


def notify(config, message):
    print(message)
    notify_config = pick(config, "通知", "notify", default={})
    notify_type = pick(notify_config, "类型", "type", default="")
    if notify_type == "官方QQ机器人":
        try:
            send_qqbot_message(notify_config, message)
        except Exception as e:
            print(f"【QQ通知失败】{e}")
        return
    webhook = pick(notify_config, "webhook地址", "webhook_url", default="")
    if webhook:
        request_json(pick(notify_config, "webhook方法", "webhook_method", default="POST"), webhook, body={"text": message})


def qqbot_access_token(notify_config):
    state = read_json(QQBOT_STATE_PATH, {})
    if state.get("access_token") and state.get("expires_at", 0) > int(time.time()) + 60:
        return state["access_token"]
    resp = request_json("POST", "https://bots.qq.com/app/getAppAccessToken", body={
        "appId": pick(notify_config, "AppID", "appid"),
        "clientSecret": pick(notify_config, "AppSecret", "appsecret"),
    })
    token = resp["access_token"]
    expires_in = int(resp.get("expires_in", 7200))
    write_json(QQBOT_STATE_PATH, {
        "access_token": token,
        "expires_at": int(time.time()) + expires_in,
    })
    return token


def send_qqbot_message(notify_config, message):
    token = qqbot_access_token(notify_config)
    openid = pick(notify_config, "用户openid", "openid")
    url = f"https://api.sgroup.qq.com/v2/users/{openid}/messages"
    request_json("POST", url, body={
        "content": message,
        "msg_type": 0,
    }, extra_headers={"Authorization": f"QQBot {token}"})


def qq_button(label, command):
    return {
        "id": command,
        "render_data": {
            "label": label,
            "visited_label": label,
            "style": 1,
        },
        "action": {
            "type": 2,
            "permission": {"type": 2},
            "data": command,
            "enter": True,
        },
    }


def send_qqbot_card(notify_config, markdown, buttons):
    token = qqbot_access_token(notify_config)
    openid = pick(notify_config, "用户openid", "openid")
    url = f"https://api.sgroup.qq.com/v2/users/{openid}/messages"
    rows = [{"buttons": row[:5]} for row in buttons[:5]]
    body = {
        "msg_type": 2,
        "markdown": {"content": markdown},
    }
    if rows:
        body["keyboard"] = {"content": {"rows": rows}}
    request_json("POST", url, body=body, extra_headers={"Authorization": f"QQBot {token}"})


def rate_direction(old_rate, rate):
    if old_rate is None:
        return "首次记录"
    old_rate = float(old_rate)
    rate = float(rate)
    if rate > old_rate:
        return "涨价"
    if rate < old_rate:
        return "降价"
    return "不变"


def print_and_notify_rates(config, site, rates, old_rates):
    name = pick(site, "名称", "name")
    if not rates:
        print("  - 没有拿到令牌绑定分组，跳过倍率检查")
        return
    for group, rate in rates.items():
        old_rate = old_rates.get(group)
        direction = rate_direction(old_rate, rate)
        print(f"  - {group}: {old_rate if old_rate is not None else '无'} -> {rate}（{direction}）")
        if old_rate is not None and direction in ("涨价", "降价"):
            notify(config, f"【分组{direction}】{name} {group}: {old_rate} -> {rate}")


def collect_group_names(data, id_to_name=None):
    groups = set()

    def add(value):
        if value is None or value == "":
            return
        if isinstance(value, int) and id_to_name and value in id_to_name:
            groups.add(id_to_name[value])
        elif isinstance(value, str):
            for part in value.replace("，", ",").split(","):
                part = part.strip()
                if part:
                    groups.add(id_to_name.get(part, part) if id_to_name else part)
        elif isinstance(value, list):
            for item in value:
                add(item)

    def walk(value):
        if isinstance(value, dict):
            for key in ("group", "group_name", "group_names", "groups", "group_id", "group_ids"):
                if key in value:
                    add(value[key])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return groups


def request_first_json(candidates, token="", extra_headers=None):
    last_error = None
    for method, url in candidates:
        try:
            return request_json(method, url, token=token, extra_headers=extra_headers)
        except Exception as e:
            last_error = e
    raise last_error


def subapi_key_groups(base, token, tz, groups):
    keys = list_subapi_tokens(base, token, tz)
    id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
    return collect_group_names(keys, id_to_name)


def list_subapi_tokens(base, token, tz):
    resp = request_first_json([
        ("GET", f"{base}/api/v1/keys?page=1&page_size=100&timezone={tz}"),
        ("GET", f"{base}/api/v1/keys?{urlencode({'page': 1, 'page_size': 100})}&timezone={tz}"),
    ], token=token)
    data = resp.get("data", resp)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def set_subapi_token_status(base, token, token_id, status):
    resp = request_json("PUT", f"{base}/api/v1/keys/{token_id}", token=token, body={"status": status})
    if resp.get("code") not in (0, None):
        raise RuntimeError(resp.get("message") or resp)
    return resp.get("data", {})


def newapi_key_groups(base, headers):
    tokens = list_newapi_tokens(base, headers)
    return collect_group_names(tokens)


def list_newapi_tokens(base, headers):
    resp = request_first_json([
        ("GET", f"{base}/api/token/?p=1&size=100"),
        ("GET", f"{base}/api/token?p=1&size=100"),
        ("GET", f"{base}/api/token/?p=0&page_size=100"),
        ("GET", f"{base}/api/token?p=0&page_size=100"),
        ("GET", f"{base}/api/token/?p=0&size=100"),
        ("GET", f"{base}/api/token?p=0&size=100"),
    ], extra_headers=headers)
    data = resp.get("data", resp)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def set_newapi_token_status(base, headers, token_id, status):
    resp = request_json("PUT", f"{base}/api/token/?status_only=true", body={
        "id": token_id,
        "status": status,
    }, extra_headers=headers)
    if not resp.get("success"):
        raise RuntimeError(resp.get("message") or resp)
    return resp.get("data", {})


def rates_for_key_groups(rates, key_groups):
    return {group: rates[group] for group in sorted(key_groups) if group in rates}


def auto_disable_subapi_tokens(config, site, base, token, tz, groups, rates, old_rates):
    threshold = pick(site, "涨价禁用阈值", "price_disable_threshold", default=None)
    if threshold in (None, ""):
        return []
    threshold = float(threshold)
    changed_groups = []
    for group, rate in rates.items():
        old_rate = old_rates.get(group)
        if old_rate in (None, 0, "0"):
            continue
        increase = (float(rate) - float(old_rate)) / float(old_rate)
        if increase >= threshold:
            changed_groups.append((group, increase))
    if not changed_groups:
        return []

    id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
    tokens = list_subapi_tokens(base, token, tz)
    disabled = []
    for group, increase in changed_groups:
        for item in tokens:
            token_group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"))
            if token_group == group and item.get("status") != "inactive":
                set_subapi_token_status(base, token, item["id"], "inactive")
                disabled.append({
                    "group": group,
                    "increase": increase,
                    "id": item["id"],
                    "name": item.get("name") or item.get("key") or str(item["id"]),
                })
    if disabled:
        lines = ["【已自动禁用令牌】", pick(site, "名称", "name")]
        for item in disabled:
            lines.append(f"{item['group']} 涨幅 {item['increase']:.0%}：{item['name']}({item['id']})")
        notify(config, "\n".join(lines))
    return disabled


def auto_disable_newapi_tokens(config, site, base, headers, rates, old_rates):
    threshold = pick(site, "涨价禁用阈值", "price_disable_threshold", default=None)
    if threshold in (None, ""):
        return []
    threshold = float(threshold)
    changed_groups = []
    for group, rate in rates.items():
        old_rate = old_rates.get(group)
        if old_rate in (None, 0, "0"):
            continue
        increase = (float(rate) - float(old_rate)) / float(old_rate)
        if increase >= threshold:
            changed_groups.append((group, increase))
    if not changed_groups:
        return []

    tokens = list_newapi_tokens(base, headers)
    disabled = []
    for group, increase in changed_groups:
        for token in tokens:
            if token.get("group") == group and int(token.get("status", 0)) != 2:
                set_newapi_token_status(base, headers, token["id"], 2)
                disabled.append({
                    "group": group,
                    "increase": increase,
                    "id": token["id"],
                    "name": token.get("name") or token.get("key") or str(token["id"]),
                })
    if disabled:
        lines = ["【已自动禁用令牌】", pick(site, "名称", "name")]
        for item in disabled:
            lines.append(f"{item['group']} 涨幅 {item['increase']:.0%}：{item['name']}({item['id']})")
        notify(config, "\n".join(lines))
    return disabled


def check_subapi(config, site, state):
    name = pick(site, "名称", "name")
    site_state = state.setdefault(name, {})
    token = site_state.get("token") or pick(site, "token", default="")
    if jwt_expired(token):
        token = login(site)
        site_state["token"] = token

    tz = quote(pick(site, "时区", "timezone", default="Asia/Hong_Kong"))
    base = pick(site, "网址", "base_url").rstrip("/")
    me = request_json("GET", f"{base}/api/v1/auth/me?timezone={tz}", token=token)
    groups = request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
    key_groups = subapi_key_groups(base, token, tz, groups)

    balance = float(me["data"]["balance"])
    old_balance = site_state.get("balance")
    site_state["balance"] = balance

    all_rates = {item["name"]: item.get("rate_multiplier") for item in groups.get("data", [])}
    rates = rates_for_key_groups(all_rates, key_groups)
    old_rates = site_state.get("rates", {})
    site_state["rates"] = rates
    auto_disable_subapi_tokens(config, site, base, token, tz, groups, rates, old_rates)

    if balance <= float(pick(site, "余额提醒线", "low_balance", default=0)):
        notify(config, f"【余额不足】{name} 当前余额：{balance}")

    print(f"{name} 检查完成：余额 {old_balance} -> {balance}，令牌绑定分组 {len(rates)} 个")
    print_and_notify_rates(config, site, rates, old_rates)


def login_newapi(site):
    base = pick(site, "网址", "base_url").rstrip("/")
    body = {
        "username": pick(site, "账号", "username", "邮箱", "email"),
        "password": pick(site, "密码", "password"),
    }
    resp, headers = request_json(
        "POST",
        f"{base}/api/user/login?turnstile=",
        body=body,
        extra_headers={"new-api-user": "-1", "origin": base},
        return_headers=True,
    )
    if not resp.get("success"):
        raise RuntimeError(f"{pick(site, '名称', 'name')} 登录失败：{resp.get('message') or resp}")
    cookies = SimpleCookie(headers.get("set-cookie", ""))
    session = cookies.get("session")
    if not session:
        raise RuntimeError(f"{pick(site, '名称', 'name')} 登录成功但没拿到 session")
    return session.value, str(resp["data"]["id"])


def newapi_headers(session, user_id):
    return {
        "cookie": f"session={session}",
        "new-api-user": user_id,
        "cache-control": "no-store",
    }


def check_newapi(config, site, state):
    name = pick(site, "名称", "name")
    site_state = state.setdefault(name, {})
    session = site_state.get("session", "")
    user_id = site_state.get("user_id", "-1")
    base = pick(site, "网址", "base_url").rstrip("/")

    try:
        me = request_json("GET", f"{base}/api/user/self", extra_headers=newapi_headers(session, user_id))
        if not me.get("success"):
            raise RuntimeError(me.get("message") or "session失效")
    except Exception:
        session, user_id = login_newapi(site)
        site_state["session"] = session
        site_state["user_id"] = user_id
        me = request_json("GET", f"{base}/api/user/self", extra_headers=newapi_headers(session, user_id))

    headers = newapi_headers(session, user_id)
    pricing = request_json("GET", f"{base}/api/pricing", extra_headers=headers)
    key_groups = newapi_key_groups(base, headers)
    quota_unit = float(pick(site, "额度单位", "quota_unit", default=500000))
    balance = float(me["data"]["quota"]) / quota_unit
    old_balance = site_state.get("balance")
    site_state["balance"] = balance

    rates = rates_for_key_groups(pricing.get("group_ratio", {}), key_groups)
    old_rates = site_state.get("rates", {})
    site_state["rates"] = rates
    auto_disable_newapi_tokens(config, site, base, headers, rates, old_rates)

    if balance <= float(pick(site, "余额提醒线", "low_balance", default=0)):
        notify(config, f"【余额不足】{name} 当前余额：{balance}")

    print(f"{name} 检查完成：余额 {old_balance} -> {balance}，令牌绑定分组 {len(rates)} 个")
    print_and_notify_rates(config, site, rates, old_rates)


def main():
    config = read_json(CONFIG_PATH, {})
    state = read_json(STATE_PATH, {})
    for site in pick(config, "网站列表", "sites", default=[]):
        if pick(site, "启用", "enabled", default=True) is False:
            continue
        try:
            site_type = pick(site, "类型", "type")
            if site_type == "subapi":
                check_subapi(config, site, state)
            elif site_type == "newapi":
                check_newapi(config, site, state)
        except HTTPError as e:
            notify(config, f"【检查失败】{pick(site, '名称', 'name')} HTTP {e.code}: {e.reason}")
        except (URLError, RuntimeError, KeyError, ValueError) as e:
            notify(config, f"【检查失败】{pick(site, '名称', 'name')}: {e}")
    write_json(STATE_PATH, state)


if __name__ == "__main__":
    main()
