# -*- coding: utf-8 -*-
import sys
import warnings
import os
import json
import datetime
import logging
import requests

# 修正 urllib3 在 Python 3.12 下引发的 SNI 丢失问题
try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_
    ssl_.HAS_SNI = True
except Exception:
    pass

import socket
# 强制使用 IPv4 避免 IPv6 黑洞
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_res = [r for r in res if r[0] == socket.AF_INET]
    return ipv4_res if ipv4_res else res
socket.getaddrinfo = _getaddrinfo_ipv4_only

warnings.filterwarnings("ignore")

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
except ImportError:
    sys.exit(1)

CONFIG_FILE = '/opt/scripts/config.json'

def billing_api_region(user):
    bill_endpoint = user.get('bill_endpoint', '')
    if 'ap-southeast-1' in bill_endpoint:
        return 'ap-southeast-1'
    return 'cn-hangzhou'

def load_config():
    if not os.path.exists(CONFIG_FILE):
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def send_tg_report(tg_conf, message):
    if not tg_conf.get('bot_token') or not tg_conf.get('chat_id'):
        return
    try:
        url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
        data = {"chat_id": tg_conf['chat_id'], "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=10)
    except:
        pass

def do_common_request(client, domain, version, action, params=None, method='POST', retries=1):
    for attempt in range(1, retries + 1):
        try:
            request = CommonRequest()
            request.set_domain(domain)
            request.set_version(version)
            request.set_action_name(action)
            request.set_method(method)
            request.set_protocol_type('https')
            request.set_connect_timeout(3000)   # 交互查询不要长时间卡住 Bot
            request.set_read_timeout(8000)
            if params:
                for k, v in params.items():
                    request.add_query_param(k, v)
            response = client.do_action_with_exception(request)
            return json.loads(response.decode('utf-8'))
        except Exception as e:
            if attempt < retries:
                import time
                time.sleep(2 * attempt)
                continue
            logging.warning("API request failed: domain=%s action=%s error=%s", domain, action, e)
            return None

def get_traffic_text(user):
    traffic_client = AcsClient(user['ak'].strip(), user['sk'].strip(), user.get('region', '').strip())
    traffic_data = do_common_request(traffic_client, 'cdt.aliyuncs.com', '2021-08-13', 'ListCdtInternetTraffic')
    traffic_gb = -1
    if traffic_data:
        traffic_gb = sum(d.get('Traffic', 0) for d in traffic_data.get('TrafficDetails', [])) / (1024**3)

    if traffic_gb >= 0:
        quota = user.get('traffic_limit', 180)
        percent = (traffic_gb / quota) * 100
        return f"{traffic_gb:.2f} GB ({percent:.1f}%)", traffic_gb
    return "⚠️ 查询失败", traffic_gb

def status_icon_for_mode(status, stopped_mode):
    mode = str(stopped_mode or '').strip()
    if status == 'Running':
        return '🟢'
    if status == 'Stopped':
        return '🔴' if mode == 'KeepCharging' else '⚫'
    if status == 'NotFound':
        return '❓'
    return '🔴'

def build_user_report(user):
    target_id = user.get('instance_id', '').strip()
    target_region = user.get('region', '').strip()
    resgroup = user.get('resgroup', '').strip()
    user_name = user.get('name', '').strip() or target_id or "Unknown_Device"


    client = AcsClient(user['ak'].strip(), user['sk'].strip(), target_region)

    traffic_str, traffic_gb = get_traffic_text(user)

    bill_amount = -1
    bill_currency = 'USD'
    bill_endpoint = user.get('bill_endpoint', 'business.ap-southeast-1.aliyuncs.com')
    bill_client = AcsClient(user['ak'].strip(), user['sk'].strip(), billing_api_region(user))

    bill_params = {
        'BillingCycle': datetime.datetime.now().strftime("%Y-%m"),
        'InstanceID': target_id
    }
    bill_data = do_common_request(bill_client, bill_endpoint, '2017-12-14', 'DescribeInstanceBill', bill_params, retries=1)
    if bill_data and bill_data.get('Success'):
        items = bill_data.get('Data', {}).get('Items', [])
        if items:
            bill_amount = sum(float(item.get('PretaxAmount', 0)) for item in items)
            bill_currency = items[0].get('Currency', 'USD')

    if bill_amount == -1:
        bill_params2 = {'BillingCycle': datetime.datetime.now().strftime("%Y-%m")}
        bill_data2 = do_common_request(bill_client, bill_endpoint, '2017-12-14', 'QueryBillOverview', bill_params2)
        if bill_data2:
            items2 = bill_data2.get('Data', {}).get('Items', {}).get('Item', [])
            bill_amount = sum(float(item.get('PretaxAmount', 0)) for item in items2)
            if items2:
                bill_currency = items2[0].get('Currency', 'USD')

    ecs_params = {'PageSize': 50, 'RegionId': target_region}
    if resgroup:
        ecs_params['ResourceGroupId'] = resgroup
    ecs_data = do_common_request(client, 'ecs.aliyuncs.com', '2014-05-26', 'DescribeInstances', ecs_params)

    status, ip, spec, stopped_mode = "NotFound", "N/A", "N/A", ""
    if ecs_data and 'Instances' in ecs_data:
        for inst in ecs_data['Instances'].get('Instance', []):
            if inst['InstanceId'] == target_id:
                status = inst.get('Status', 'Unknown')
                stopped_mode = inst.get('StoppedMode', '')
                pub = inst.get('PublicIpAddress', {}).get('IpAddress', [])
                eip = inst.get('EipAddress', {}).get('IpAddress', "")
                ip = eip if eip else (pub[0] if pub else "无公网IP")
                cpu = inst.get('Cpu', 0)
                mem_mb = inst.get('Memory', 0)
                if mem_mb > 0 and mem_mb % 1024 == 0:
                    mem_str = f"{int(mem_mb/1024)}"
                else:
                    mem_str = f"{mem_mb/1024:.1f}"
                spec = f"{cpu}C{mem_str}G"
                break

    monitor_state = "⏸️ 已暂停" if (user.get('paused') or user.get('disabled')) else "▶️ 运行中"
    quota = user.get('traffic_limit', 180)
    bill_limit = user.get('bill_threshold', 1.0)
    if user.get('schedule_enabled'):
        schedule_str = f"{user.get('schedule_start', '00:00')}-{user.get('schedule_end', '23:59')}"
    else:
        schedule_str = "全天运行"

    bill_str = f"${bill_amount:.2f}" if bill_amount != -1 else "Fail"
    if bill_amount != -1 and bill_currency == 'CNY':
        bill_str = f"¥{bill_amount:.2f}"
        bill_limit = bill_limit * 7.0
    elif bill_amount != -1:
        currency_symbol = user.get('currency', '$')
        bill_str = f"{currency_symbol}{bill_amount:.2f}"

    risk_str = "✅ 无风险"
    if traffic_gb >= 0 and traffic_gb > quota:
        risk_str = "⚠️ 流量超标"
    if bill_amount > bill_limit:
        risk_str = "💸 扣费预警"
    if traffic_gb < 0:
        risk_str = "⚠️ 流量查询异常"

    run_icon = status_icon_for_mode(status, stopped_mode)

    return (
        f"☁️ *{user_name}* ({spec})\n"
        f"├🖥️ 状态: {run_icon} {status}\n"
        f"├🌐 IP: `{ip}`\n"
        f"├🛡️ 监控: {monitor_state}\n"
        f"├⏱️ 计划: {schedule_str}\n"
        f"├📈 流量: {traffic_str}\n"
        f"├💰 账单: *{bill_str}*\n"
        f"├🔥 预警: {risk_str}\n"
    )


def build_report(config):
    users = config.get('users', [])
    
    report_lines = []
    now = datetime.datetime.now()
    update_time = now.strftime("%Y-%m-%d %H:%M")
    report_lines.append("📊 *阿里云 - 每日报告*\n")

    for user in users:
        try:
            report_lines.append(build_user_report(user))
        except Exception as e:
            report_lines.append(f"❌ *{user.get('name', 'Unknown')}* Error: {str(e)}\n")

    report_lines.append(f"\n⏰ 更新时间：{update_time}")
    return "\n".join(report_lines)

def main():
    config = load_config()
    tg_conf = config.get('telegram', {})
    send_tg_report(tg_conf, build_report(config))

if __name__ == "__main__":
    main()
