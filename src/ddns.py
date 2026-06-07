# -*- coding: utf-8 -*-
import time

import requests


DDNS_SYNC_INTERVAL = 3600
CF_API_BASE = "https://api.cloudflare.com/client/v4"


def is_ddns_enabled(user):
    return bool(user.get("ddns_enabled"))


def instance_public_ip(instance):
    if not instance:
        return ""
    eip = instance.get("EipAddress", {}).get("IpAddress", "")
    public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", [])
    return eip or (public_ips[0] if public_ips else "")


def ddns_desc(user):
    if not is_ddns_enabled(user):
        return "未启用"
    record_name = user.get("ddns_record_name", "")
    return record_name or "未配置"


def ddns_record_key(user):
    if not is_ddns_enabled(user):
        return ""
    record_name = str(user.get("ddns_record_name", "")).strip().lower()
    zone_id = str(user.get("ddns_zone_id", "")).strip()
    record_type = str(user.get("ddns_record_type", "A")).strip().upper() or "A"
    provider = str(user.get("ddns_provider", "cloudflare")).strip().lower()
    if not record_name:
        return ""
    return f"{provider}:{zone_id}:{record_type}:{record_name}"


def should_sync_ddns(user, state, instance_id, public_ip, force=False):
    if force:
        return True
    if not public_ip:
        return False
    item = state.setdefault(instance_id, {})
    last_ip = item.get("last_ddns_ip")
    last_ts = float(item.get("last_ddns_sync_ts", 0) or 0)
    return public_ip != last_ip or time.time() - last_ts >= DDNS_SYNC_INTERVAL


def sync_ddns_if_needed(user, state, instance_id, public_ip, force=False, logger=None):
    if not is_ddns_enabled(user):
        return None
    if not should_sync_ddns(user, state, instance_id, public_ip, force=force):
        return None
    result = sync_ddns(user, public_ip, logger=logger)
    if result.get("ok"):
        item = state.setdefault(instance_id, {})
        item["last_ddns_ip"] = public_ip
        item["last_ddns_sync_ts"] = time.time()
        item["last_ddns_record"] = result.get("record_name", "")
    return result


def sync_ddns(user, public_ip, logger=None):
    provider = str(user.get("ddns_provider", "cloudflare")).lower()
    if provider != "cloudflare":
        return _result(False, f"DDNS: 不支持的服务商 {provider}")
    if not public_ip:
        return _result(False, "DDNS: 未获取到公网 IP")

    token = str(user.get("ddns_token", "")).strip()
    zone_id = str(user.get("ddns_zone_id", "")).strip()
    record_name = str(user.get("ddns_record_name", "")).strip()
    record_type = str(user.get("ddns_record_type", "A")).strip().upper() or "A"
    proxied = bool(user.get("ddns_proxied", False))

    if not token or not zone_id or not record_name:
        return _result(False, "DDNS: Cloudflare 配置不完整")
    if record_type != "A":
        return _result(False, f"DDNS: 第一版仅支持 A 记录，当前为 {record_type}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        record = _find_cf_record(zone_id, record_type, record_name, headers, logger=logger)
        if record and record.get("content") == public_ip and bool(record.get("proxied", False)) == proxied:
            return _result(True, f"DDNS: {record_name} 已是最新 -> {public_ip}", False, record_name, public_ip)

        payload = {
            "type": record_type,
            "name": record_name,
            "content": public_ip,
            "ttl": 1,
            "proxied": proxied,
        }
        if record:
            resp = requests.put(
                f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record['id']}",
                headers=headers,
                json=payload,
                timeout=10,
            )
            action = "已同步"
        else:
            resp = requests.post(
                f"{CF_API_BASE}/zones/{zone_id}/dns_records",
                headers=headers,
                json=payload,
                timeout=10,
            )
            action = "已创建并同步"

        data = _cf_json(resp)
        if not resp.ok or not data.get("success"):
            err = _cf_error(data, resp.status_code)
            return _result(False, f"DDNS: {record_name} 同步失败，{err}", True, record_name, public_ip)
        return _result(True, f"DDNS: {record_name} -> {public_ip} {action}", True, record_name, public_ip)
    except Exception as exc:
        if logger:
            logger.warning("[%s] DDNS 同步异常: %s", user.get("name", user.get("instance_id")), exc)
        return _result(False, f"DDNS: 同步异常 {exc}", True, record_name, public_ip)


def _find_cf_record(zone_id, record_type, record_name, headers, logger=None):
    resp = requests.get(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"type": record_type, "name": record_name, "per_page": 1},
        timeout=10,
    )
    data = _cf_json(resp)
    if logger:
        logger.info("Cloudflare DNS 查询 %s %s: HTTP %s", record_type, record_name, resp.status_code)
    if not resp.ok or not data.get("success"):
        raise RuntimeError(_cf_error(data, resp.status_code))
    records = data.get("result") or []
    return records[0] if records else None


def _cf_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"success": False, "errors": [{"message": resp.text[:200]}]}


def _cf_error(data, status_code):
    errors = data.get("errors") or []
    if errors:
        return "; ".join(str(e.get("message", e)) for e in errors)
    return f"Cloudflare HTTP {status_code}"


def _result(ok, message, changed=False, record_name="", ip=""):
    return {
        "ok": ok,
        "message": message,
        "changed": changed,
        "record_name": record_name,
        "ip": ip,
    }
