# -*- coding: utf-8 -*-
import datetime
import logging
import os
import sys
import requests
from logging.handlers import RotatingFileHandler

from common import (
    AcsClient,
    do_common_request,
    load_config,
    query_account_balance,
    query_cdt_traffic,
    query_instance_bill,
)
from ddns import ddns_desc

LOG_FILE = "/opt/scripts/report.log"
logger = logging.getLogger()
logger.setLevel(logging.INFO)
if not logger.handlers:
    report_handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    report_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(report_handler)


def send_tg_report(tg_conf, message):
    if not tg_conf.get("bot_token") or not tg_conf.get("chat_id"):
        return
    try:
        url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
        data = {"chat_id": tg_conf["chat_id"], "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        logger.error("发送 TG 日报失败: %s", e)


def get_traffic_text(user):
    traffic_gb = query_cdt_traffic(user)
    if traffic_gb >= 0:
        limit = user.get("traffic_limit", 180)
        percent = (traffic_gb / limit) * 100 if limit > 0 else 0
        return f"{traffic_gb:.2f} GB ({percent:.1f}%)", traffic_gb
    return "⚠️ 查询失败", traffic_gb


def status_icon_for_mode(status, stopped_mode):
    mode = str(stopped_mode or "").strip()
    if status == "Running":
        return "🟢"
    if status == "Stopped":
        return "🔴" if mode == "KeepCharging" else "⚫"
    if status == "NotFound":
        return "❓"
    return "🔴"


def override_desc(user):
    override = user.get("manual_override")
    if override == "run":
        return "手动保持运行"
    if override == "stop":
        return "手动保持停机"
    return ""


def build_user_report(user, balance_cache=None):
    target_id = user.get("instance_id", "").strip()
    target_region = user.get("region", "").strip()
    resgroup = user.get("resgroup", "").strip()
    user_name = user.get("name", "").strip() or target_id or "Unknown_Device"

    client = AcsClient(user["ak"].strip(), user["sk"].strip(), target_region) if AcsClient else None

    # 1. CDT 流量
    traffic_str, traffic_gb = get_traffic_text(user)

    # 2. 实例账单
    bill_amount, bill_currency = query_instance_bill(user, target_id)

    # 3. 账户可用余额（支持同 AK 缓存）
    balance_str = ""
    ak_key = user["ak"].strip()
    if balance_cache is not None:
        if ak_key not in balance_cache:
            balance_cache[ak_key] = query_account_balance(user)
        avail_bal, bal_curr = balance_cache[ak_key]
        if avail_bal is not None:
            curr_sym = "¥" if bal_curr == "CNY" else ("$" if bal_curr == "USD" else (bal_curr or ""))
            balance_str = f" | 💳 余额: {curr_sym}{avail_bal:.2f}"

    # 4. ECS 状态
    ecs_params = {"PageSize": 50, "RegionId": target_region}
    if resgroup:
        ecs_params["ResourceGroupId"] = resgroup
    ecs_data = do_common_request(client, "ecs.aliyuncs.com", "2014-05-26", "DescribeInstances", ecs_params)

    status, ip, spec, stopped_mode = "NotFound", "N/A", "N/A", ""
    if ecs_data and "Instances" in ecs_data:
        for inst in ecs_data["Instances"].get("Instance", []):
            if inst["InstanceId"] == target_id:
                status = inst.get("Status", "Unknown")
                stopped_mode = inst.get("StoppedMode", "")
                pub = inst.get("PublicIpAddress", {}).get("IpAddress", [])
                eip = inst.get("EipAddress", {}).get("IpAddress", "")
                ip = eip if eip else (pub[0] if pub else "无公网IP")
                cpu = inst.get("Cpu", 0)
                mem_mb = inst.get("Memory", 0)
                if mem_mb > 0 and mem_mb % 1024 == 0:
                    mem_str = f"{int(mem_mb/1024)}"
                else:
                    mem_str = f"{mem_mb/1024:.1f}"
                spec = f"{cpu}C{mem_str}G"
                break

    monitor_state = "⏸️ 已暂停" if (user.get("paused") or user.get("disabled")) else "▶️ 运行中"
    limit = user.get("traffic_limit", 180)
    bill_limit = user.get("bill_threshold", 1.0)
    if user.get("schedule_enabled"):
        schedule_str = f"{user.get('schedule_start', '00:00')}-{user.get('schedule_end', '23:59')}"
    else:
        schedule_str = "全天运行"

    bill_str = f"${bill_amount:.2f}" if bill_amount != -1 else "Fail"
    if bill_amount != -1 and bill_currency == "CNY":
        bill_str = f"¥{bill_amount:.2f}"
        bill_limit = bill_limit * 7.0
    elif bill_amount != -1:
        currency_symbol = user.get("currency", "$")
        bill_str = f"{currency_symbol}{bill_amount:.2f}"

    risk_str = "✅ 无风险"
    if traffic_gb >= 0 and traffic_gb > limit:
        risk_str = "⚠️ 流量超标"
    if bill_amount >= bill_limit:
        risk_str = "💸 扣费预警"
    if traffic_gb < 0:
        risk_str = "⚠️ 流量查询异常"

    override_status = override_desc(user)
    if override_status:
        status_line = f"🔴 {override_status}"
    else:
        status_line = f"{status_icon_for_mode(status, stopped_mode)} {status}"

    return (
        f"☁️ *{user_name}* ({spec})\n"
        f"├🖥️ 状态: {status_line}\n"
        f"├🌐 IP: `{ip}`\n"
        f"├🌍 DDNS: {ddns_desc(user)}\n"
        f"├🛡️ 监控: {monitor_state}\n"
        f"├⏱️ 计划: {schedule_str}\n"
        f"├📈 流量: {traffic_str}\n"
        f"├💰 账单: *{bill_str}*{balance_str}\n"
        f"├🔥 预警: {risk_str}\n"
    )


def build_report(config):
    users = config.get("users", [])
    report_lines = ["📊 *阿里云 - 每日报告*\n"]
    now = datetime.datetime.now()
    update_time = now.strftime("%Y-%m-%d %H:%M")
    balance_cache = {}

    for user in users:
        try:
            report_lines.append(build_user_report(user, balance_cache))
        except Exception as e:
            report_lines.append(f"❌ *{user.get('name', 'Unknown')}* Error: {str(e)}\n")

    report_lines.append(f"\n⏰ 更新时间：{update_time}")
    return "\n".join(report_lines)


def main():
    config = load_config()
    tg_conf = config.get("telegram", {})
    send_tg_report(tg_conf, build_report(config))


if __name__ == "__main__":
    main()
