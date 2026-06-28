import contextlib
import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
import websocket

import monitor


def heartbeat(ws, interval_ms):
    while True:
        time.sleep(interval_ms / 1000)
        ws.send(json.dumps({"op": 1, "d": None}))


def find_value(data, *keys):
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key]
        for value in data.values():
            found = find_value(value, *keys)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = find_value(item, *keys)
            if found:
                return found
    return ""


def card_to_text(card):
    text = card.get("markdown", "")
    return (
        text.replace("# ", "")
        .replace("## ", "")
        .replace("**", "")
        .replace("`", "")
        .replace("- ", "")
    )


def send_reply(notify_config, reply):
    items = reply if isinstance(reply, list) else [reply]
    for item in items:
        try:
            if isinstance(item, dict) and item.get("type") == "card":
                monitor.send_qqbot_card(notify_config, item["markdown"], item["buttons"])
            else:
                monitor.send_qqbot_message(notify_config, str(item)[:1800])
        except Exception as e:
            print(f"【发送失败】{e}")
            if isinstance(item, dict):
                try:
                    monitor.send_qqbot_message(notify_config, card_to_text(item)[:1800])
                except Exception as text_error:
                    print(f"【文本兜底也失败】{text_error}")


def group_status_buttons(site):
    site_name = monitor.pick(site, "名称", "name")
    site_type = monitor.pick(site, "类型", "type")
    groups = {}
    try:
        if site_type == "newapi":
            base, headers = newapi_session(site)
            for token in monitor.list_newapi_tokens(base, headers):
                group = token.get("group") or "未分组"
                groups.setdefault(group, []).append(int(token.get("status", 0)) == 1)
        else:
            base, token, tz = subapi_session(site)
            group_resp = monitor.request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
            id_to_name = {item["id"]: item["name"] for item in group_resp.get("data", []) if "id" in item and "name" in item}
            for item in monitor.list_subapi_tokens(base, token, tz):
                group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"), "未分组")
                groups.setdefault(group, []).append(item.get("status") == "active")
    except Exception as e:
        print(f"【分组按钮生成失败】{site_name}: {e}")
        return []

    buttons = []
    for group, statuses in sorted(groups.items()):
        enabled = any(statuses)
        label = f"✅ {group}" if enabled else f"✖ {group}"
        action = "禁用分组" if enabled else "启用分组"
        buttons.append(monitor.qq_button(label, f"{action}|{site_name}|{group}"))
    return buttons


def chunked(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def fmt_balance(value):
    return f"{float(value):.2f}" if isinstance(value, (int, float)) else value


def site_priority(site, item, old_item=None):
    balance = item.get("balance")
    low_balance = float(monitor.pick(site, "余额提醒线", "low_balance", default=0))
    if isinstance(balance, (int, float)) and balance <= low_balance:
        return 0
    old_rates = (old_item or {}).get("rates", {})
    for group, rate in item.get("rates", {}).items():
        if monitor.rate_direction(old_rates.get(group), rate) in ("涨价", "降价"):
            return 1
    return 2


def site_card(site, item, old_item=None):
    name = monitor.pick(site, "名称", "name")
    raw_balance = item.get("balance", "暂无")
    balance = fmt_balance(raw_balance)
    low_balance = float(monitor.pick(site, "余额提醒线", "low_balance", default=0))
    low_mark = " ⚠️" if isinstance(raw_balance, (int, float)) and raw_balance <= low_balance else ""
    old_rates = (old_item or {}).get("rates", {})
    lines = [f"# {name}", "", f"💰 **{balance}**{low_mark}", ""]
    rates = item.get("rates", {})
    if rates:
        for group, rate in rates.items():
            direction = monitor.rate_direction(old_rates.get(group), rate)
            suffix = ""
            if direction == "涨价":
                suffix = "  🔺"
            elif direction == "降价":
                suffix = "  🔻"
            lines.append(f"▸ {group}  `{rate}`{suffix}")
    else:
        lines.append("暂无绑定分组")
    buttons = chunked(group_status_buttons(site), 2)
    return {"type": "card", "markdown": "\n".join(lines), "buttons": buttons}


def site_cards(config, state, old_state=None, sites=None):
    sites = sites or monitor.pick(config, "网站列表", "sites", default=[])
    cards = []
    for site in sites:
        name = monitor.pick(site, "名称", "name")
        item = state.get(name, {})
        old_item = (old_state or {}).get(name, {})
        cards.append((site_priority(site, item, old_item), name, site_card(site, item, old_item)))
    return [card for _, _, card in sorted(cards, key=lambda item: (item[0], item[1]))]


def state_text(config):
    state = monitor.read_json(monitor.STATE_PATH, {})
    lines = ["📊 上游状态", ""]
    for site in monitor.pick(config, "网站列表", "sites", default=[]):
        name = monitor.pick(site, "名称", "name")
        item = state.get(name, {})
        balance = fmt_balance(item.get("balance", "暂无"))
        lines.append(f"{name}｜余额 {balance}")
        rates = item.get("rates", {})
        if rates:
            lines.extend([f"{group} {rate}" for group, rate in rates.items()])
        else:
            lines.append("暂无令牌绑定分组倍率")
        lines.append("")
    return "\n".join(lines) or "暂无状态"


def state_cards(config):
    state = monitor.read_json(monitor.STATE_PATH, {})
    return site_cards(config, state)


def compact_result_text(config, old_state, new_state, sites):
    blocks = []
    has_change = False
    for site in sites:
        name = monitor.pick(site, "名称", "name")
        item = new_state.get(name, {})
        old_rates = old_state.get(name, {}).get("rates", {})
        raw_balance = item.get("balance", "暂无")
        balance = fmt_balance(raw_balance)
        low_balance = float(monitor.pick(site, "余额提醒线", "low_balance", default=0))
        low_mark = ""
        if isinstance(raw_balance, (int, float)) and raw_balance <= low_balance:
            has_change = True
            low_mark = " ⚠️ 低余额"
        blocks.append(f"{name}｜余额 {balance}{low_mark}")
        rates = item.get("rates", {})
        if rates:
            for group, rate in rates.items():
                old_rate = old_rates.get(group)
                direction = monitor.rate_direction(old_rate, rate)
                suffix = ""
                if direction == "涨价":
                    has_change = True
                    suffix = " ↑ 涨价"
                elif direction == "降价":
                    has_change = True
                    suffix = " ↓ 降价"
                blocks.append(f"{group} {rate}{suffix}")
        else:
            blocks.append("暂无令牌绑定分组倍率")
        blocks.append("")
    title = "⚠️ 发现变动" if has_change else "✅ 上游正常"
    return f"{title}\n\n" + "\n".join(blocks).strip()


def check_sites_text(config, keyword=""):
    state = monitor.read_json(monitor.STATE_PATH, {})
    old_state = json.loads(json.dumps(state, ensure_ascii=False))
    sites = monitor.pick(config, "网站列表", "sites", default=[])
    if keyword:
        sites = [site for site in sites if keyword.lower() in monitor.pick(site, "名称", "name", default="").lower()]
    if not sites:
        return f"没找到站点：{keyword}"

    silent_config = dict(config)
    silent_config["通知"] = {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for site in sites:
            if monitor.pick(site, "启用", "enabled", default=True) is False:
                continue
            try:
                site_type = monitor.pick(site, "类型", "type")
                if site_type == "subapi":
                    monitor.check_subapi(silent_config, site, state)
                elif site_type == "newapi":
                    monitor.check_newapi(silent_config, site, state)
            except Exception as e:
                print(f"【检查失败】{monitor.pick(site, '名称', 'name')}: {e}")
    monitor.write_json(monitor.STATE_PATH, state)
    errors = "\n".join(line for line in buf.getvalue().splitlines() if "检查失败" in line)
    text = compact_result_text(config, old_state, state, sites)
    return f"{text}\n{errors}".strip() if errors else text


def check_sites_cards(config, keyword=""):
    state = monitor.read_json(monitor.STATE_PATH, {})
    old_state = json.loads(json.dumps(state, ensure_ascii=False))
    sites = monitor.pick(config, "网站列表", "sites", default=[])
    if keyword:
        sites = [site for site in sites if keyword.lower() in monitor.pick(site, "名称", "name", default="").lower()]
    if not sites:
        return f"没找到站点：{keyword}"

    silent_config = dict(config)
    silent_config["通知"] = {}
    errors = []
    with contextlib.redirect_stdout(io.StringIO()):
        for site in sites:
            if monitor.pick(site, "启用", "enabled", default=True) is False:
                continue
            try:
                site_type = monitor.pick(site, "类型", "type")
                if site_type == "subapi":
                    monitor.check_subapi(silent_config, site, state)
                elif site_type == "newapi":
                    monitor.check_newapi(silent_config, site, state)
            except Exception as e:
                errors.append(f"【检查失败】{monitor.pick(site, '名称', 'name')}: {e}")
    monitor.write_json(monitor.STATE_PATH, state)
    cards = site_cards(config, state, old_state, sites)
    if errors:
        cards.append("\n".join(errors))
    return cards


def matching_sites(config, keyword, site_type=""):
    sites = []
    for site in monitor.pick(config, "网站列表", "sites", default=[]):
        name = monitor.pick(site, "名称", "name", default="")
        if (not site_type or monitor.pick(site, "类型", "type") == site_type) and keyword.lower() in name.lower():
            sites.append(site)
    return sites


def subapi_session(site):
    state = monitor.read_json(monitor.STATE_PATH, {})
    name = monitor.pick(site, "名称", "name")
    item = state.setdefault(name, {})
    token = item.get("token") or monitor.pick(site, "token", default="")
    if monitor.jwt_expired(token):
        token = monitor.login(site)
        item["token"] = token
        monitor.write_json(monitor.STATE_PATH, state)
    base = monitor.pick(site, "网址", "base_url").rstrip("/")
    tz = monitor.quote(monitor.pick(site, "时区", "timezone", default="Asia/Hong_Kong"))
    return base, token, tz


def newapi_session(site):
    state = monitor.read_json(monitor.STATE_PATH, {})
    name = monitor.pick(site, "名称", "name")
    item = state.setdefault(name, {})
    session = item.get("session", "")
    user_id = item.get("user_id", "-1")
    base = monitor.pick(site, "网址", "base_url").rstrip("/")
    headers = monitor.newapi_headers(session, user_id)
    try:
        me = monitor.request_json("GET", f"{base}/api/user/self", extra_headers=headers)
        if not me.get("success"):
            raise RuntimeError("session失效")
    except Exception:
        session, user_id = monitor.login_newapi(site)
        item["session"] = session
        item["user_id"] = user_id
        monitor.write_json(monitor.STATE_PATH, state)
        headers = monitor.newapi_headers(session, user_id)
    return base, headers


def token_list_text(config, keyword):
    sites = matching_sites(config, keyword)
    if not sites:
        return f"没找到站点：{keyword}"
    lines = []
    for site in sites:
        site_type = monitor.pick(site, "类型", "type")
        site_name = monitor.pick(site, "名称", "name")
        lines.append(site_name)
        if site_type == "newapi":
            base, headers = newapi_session(site)
            tokens = monitor.list_newapi_tokens(base, headers)
            rows = [(token.get("name") or token.get("key"), token.get("group"), "启用" if int(token.get("status", 0)) == 1 else "禁用") for token in tokens]
        else:
            base, token, tz = subapi_session(site)
            groups = monitor.request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
            id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
            tokens = monitor.list_subapi_tokens(base, token, tz)
            rows = []
            for item in tokens:
                group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"))
                rows.append((item.get("name") or item.get("key"), group, "启用" if item.get("status") == "active" else "禁用"))
        lines.extend([f"{name}｜{group}｜{status}" for name, group, status in rows])
        lines.append("")
    return "\n".join(lines).strip()


def token_card(site, token_name, group, status_text, command):
    site_name = monitor.pick(site, "名称", "name")
    lines = [
        f"# {token_name}",
        "",
        f"站点：**{site_name}**",
        f"分组：`{group}`",
        f"状态：**{status_text}**",
    ]
    action = "启用" if status_text == "禁用" else "禁用"
    buttons = [
        [monitor.qq_button(f"{action}令牌", command), monitor.qq_button("刷新令牌", f"令牌 {site_name}")],
        [monitor.qq_button("刷新本站", f"查 {site_name}")],
    ]
    return {"type": "card", "markdown": "\n".join(lines), "buttons": buttons}


def token_list_cards(config, keyword):
    sites = matching_sites(config, keyword)
    if not sites:
        return f"没找到站点：{keyword}"
    cards = []
    for site in sites:
        site_type = monitor.pick(site, "类型", "type")
        site_name = monitor.pick(site, "名称", "name")
        if site_type == "newapi":
            base, headers = newapi_session(site)
            for token in monitor.list_newapi_tokens(base, headers):
                token_name = token.get("name") or token.get("key")
                status_text = "启用" if int(token.get("status", 0)) == 1 else "禁用"
                action = "启用令牌" if status_text == "禁用" else "禁用令牌"
                cards.append(token_card(site, token_name, token.get("group"), status_text, f"{action} {token['id']} {site_name}"))
        else:
            base, token, tz = subapi_session(site)
            groups = monitor.request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
            id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
            for item in monitor.list_subapi_tokens(base, token, tz):
                token_name = item.get("name") or item.get("key")
                group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"))
                status_text = "启用" if item.get("status") == "active" else "禁用"
                action = "启用令牌" if status_text == "禁用" else "禁用令牌"
                cards.append(token_card(site, token_name, group, status_text, f"{action} {item['id']} {site_name}"))
    return cards or f"{keyword} 没有令牌"


def set_token_by_id_text(config, command, token_id, site_keyword):
    action = "启用" if command == "启用令牌" else "禁用"
    status = 1 if action == "启用" else 2
    sites = matching_sites(config, site_keyword)
    if not sites:
        return f"没找到站点：{site_keyword}"
    site = sites[0]
    site_type = monitor.pick(site, "类型", "type")
    site_name = monitor.pick(site, "名称", "name")
    if site_type == "newapi":
        base, headers = newapi_session(site)
        data = monitor.set_newapi_token_status(base, headers, int(token_id), status)
        token_name = data.get("name") or data.get("key") or token_id
        group = data.get("group", "")
    else:
        base, token, _tz = subapi_session(site)
        sub_status = "active" if status == 1 else "inactive"
        data = monitor.set_subapi_token_status(base, token, int(token_id), sub_status)
        token_name = data.get("name") or data.get("key") or token_id
        group = data.get("group", {}).get("name") if isinstance(data.get("group"), dict) else data.get("group_id", "")
    return f"{site_name}\n已{action}：{token_name}｜{group}"


def set_tokens_text(config, command, keyword):
    action = "启用" if command == "启用" else "禁用"
    status = 1 if action == "启用" else 2
    sites = matching_sites(config, keyword)
    if not sites:
        sites = [
            site for site in monitor.pick(config, "网站列表", "sites", default=[])
        ]
    lines = []
    for site in sites:
        site_type = monitor.pick(site, "类型", "type")
        changed = []
        site_name = monitor.pick(site, "名称", "name", default="")
        whole_site = keyword.lower() in site_name.lower()
        if site_type == "newapi":
            base, headers = newapi_session(site)
            tokens = monitor.list_newapi_tokens(base, headers)
            for token in tokens:
                group = token.get("group", "")
                if whole_site or keyword.lower() in group.lower():
                    if int(token.get("status", 0)) != status:
                        monitor.set_newapi_token_status(base, headers, token["id"], status)
                        changed.append(f"{token.get('name') or token.get('key')}｜{group}")
        else:
            base, token, tz = subapi_session(site)
            groups = monitor.request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
            id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
            tokens = monitor.list_subapi_tokens(base, token, tz)
            sub_status = "active" if status == 1 else "inactive"
            for item in tokens:
                group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"), "")
                if whole_site or keyword.lower() in group.lower():
                    if item.get("status") != sub_status:
                        monitor.set_subapi_token_status(base, token, item["id"], sub_status)
                        changed.append(f"{item.get('name') or item.get('key')}｜{group}")
        if changed or whole_site:
            lines.append(site_name)
            lines.extend([f"已{action}：{item}" for item in changed] or ["没有需要处理的令牌"])
            lines.append("")
    if not lines:
        return f"没找到匹配令牌：{keyword}"
    return "\n".join(lines).strip()


def set_group_tokens_text(config, command, site_keyword, group_keyword):
    action = "启用" if command == "启用分组" else "禁用"
    status = 1 if action == "启用" else 2
    sites = matching_sites(config, site_keyword)
    if not sites:
        return f"没找到站点：{site_keyword}"
    lines = []
    for site in sites:
        site_type = monitor.pick(site, "类型", "type")
        site_name = monitor.pick(site, "名称", "name", default="")
        changed = []
        if site_type == "newapi":
            base, headers = newapi_session(site)
            for token in monitor.list_newapi_tokens(base, headers):
                group = token.get("group", "")
                if group == group_keyword and int(token.get("status", 0)) != status:
                    monitor.set_newapi_token_status(base, headers, token["id"], status)
                    changed.append(token.get("name") or token.get("key"))
        else:
            base, token, tz = subapi_session(site)
            groups = monitor.request_json("GET", f"{base}/api/v1/groups/available?timezone={tz}", token=token)
            id_to_name = {item["id"]: item["name"] for item in groups.get("data", []) if "id" in item and "name" in item}
            sub_status = "active" if status == 1 else "inactive"
            for item in monitor.list_subapi_tokens(base, token, tz):
                group = item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else id_to_name.get(item.get("group_id"), "")
                if group == group_keyword and item.get("status") != sub_status:
                    monitor.set_subapi_token_status(base, token, item["id"], sub_status)
                    changed.append(item.get("name") or item.get("key"))
        lines.append(site_name)
        lines.append(f"{group_keyword}")
        lines.extend([f"已{action}：{name}" for name in changed] or ["没有需要处理的令牌"])
        lines.append("")
    return "\n".join(lines).strip()


def handle_command(config, content):
    content = content.strip().replace("\u00a0", " ")
    if content in ("帮助", "help", "/help"):
        return "发送“状态”查看上游状态。\n卡片按钮可以切换分组令牌状态。"
    if content in ("状态", "查状态"):
        return state_cards(config)
    if content in ("查全部", "立即检查"):
        return check_sites_cards(config)
    if content.startswith("查 "):
        return check_sites_cards(config, content[2:].strip())
    if content.startswith("令牌 "):
        return token_list_cards(config, content[3:].strip())
    if content.startswith("禁用分组|") or content.startswith("启用分组|"):
        parts = content.split("|", 2)
        if len(parts) == 3:
            return set_group_tokens_text(config, parts[0], parts[1], parts[2])
    if content.startswith("禁用令牌 "):
        parts = content.split(maxsplit=2)
        if len(parts) == 3:
            token_id, site_name = parts[1], parts[2]
            return set_token_by_id_text(config, "禁用令牌", token_id, site_name)
    if content.startswith("启用令牌 "):
        parts = content.split(maxsplit=2)
        if len(parts) == 3:
            token_id, site_name = parts[1], parts[2]
            return set_token_by_id_text(config, "启用令牌", token_id, site_name)
    if content.startswith("禁用 "):
        return set_tokens_text(config, "禁用", content[3:].strip())
    if content.startswith("启用 "):
        return set_tokens_text(config, "启用", content[3:].strip())
    return "没识别这个指令。发送“帮助”查看可用指令。"


def main():
    config = monitor.read_json(monitor.CONFIG_PATH, {})
    notify_config = monitor.pick(config, "通知", "notify", default={})
    my_openid = monitor.pick(notify_config, "用户openid", "openid")
    if not my_openid:
        print("先在 config.json 里填 用户openid")
        return

    token = monitor.qqbot_access_token(notify_config)
    gateway = monitor.request_json("GET", "https://api.sgroup.qq.com/gateway", extra_headers={
        "Authorization": f"QQBot {token}",
    })["url"]

    ws = websocket.WebSocket()
    ws.connect(gateway)
    hello = json.loads(ws.recv())
    interval = hello.get("d", {}).get("heartbeat_interval", 45000)
    threading.Thread(target=heartbeat, args=(ws, interval), daemon=True).start()
    ws.send(json.dumps({
        "op": 2,
        "d": {
            "token": f"QQBot {token}",
            "intents": (1 << 25) | (1 << 12),
            "shard": [0, 1],
            "properties": {"os": "windows", "browser": "monitor", "device": "monitor"},
        }
    }))

    print("QQ查询机器人已启动。给机器人发：状态")
    while True:
        msg = json.loads(ws.recv())
        data = msg.get("d", {})
        sender_openid = find_value(data, "user_openid", "openid")
        content = find_value(data, "content")
        if msg.get("t") and content:
            print(f"收到消息：openid={sender_openid} content={content}")
            if sender_openid == my_openid:
                reply = handle_command(config, content)
                if reply:
                    send_reply(notify_config, reply)


if __name__ == "__main__":
    main()
