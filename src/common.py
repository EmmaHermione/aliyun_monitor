# -*- coding: utf-8 -*-
import datetime
import json
import logging
import os
import socket
import tempfile
import time
import warnings

# 修正 urllib3 在 Python 3.12 下引发的 SNI 丢失问题
try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_
    ssl_.HAS_SNI = True
except Exception:
    pass

# 强制使用 IPv4 避免 IPv6 黑洞
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_res = [r for r in res if r[0] == socket.AF_INET]
    return ipv4_res if ipv4_res else res


socket.getaddrinfo = _getaddrinfo_ipv4_only

warnings.filterwarnings("ignore")

CONFIG_FILE = "/opt/scripts/config.json"
MONITOR_STATE_FILE = "/opt/scripts/monitor_state.json"
BOT_STATE_FILE = "/opt/scripts/bot_state.json"

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
except ImportError:
    AcsClient = None
    CommonRequest = None


def load_json(file_path, default_value=None):
    if default_value is None:
        default_value = {}
    if not os.path.exists(file_path):
        return default_value
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error("读取 JSON 文件 %s 失败: %s", file_path, e)
        return default_value


def save_json(file_path, data):
    directory = os.path.dirname(file_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, file_path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logging.error("保存 JSON 文件 %s 失败: %s", file_path, e)
        raise


def load_config():
    return load_json(CONFIG_FILE, {})


def save_config(config):
    save_json(CONFIG_FILE, config)


def billing_api_region(user=None):
    return "cn-hangzhou"


def do_common_request(client, domain, version, action, params=None, method="POST", retries=1):
    if client is None or CommonRequest is None:
        return None
    for attempt in range(1, retries + 1):
        try:
            request = CommonRequest()
            request.set_domain(domain)
            request.set_version(version)
            request.set_action_name(action)
            request.set_method(method)
            request.set_protocol_type("https")
            request.set_connect_timeout(5000)
            request.set_read_timeout(15000)
            if params:
                for k, v in params.items():
                    request.add_query_param(k, v)
            resp = client.do_action_with_exception(request)
            return json.loads(resp.decode("utf-8"))
        except Exception as e:
            if attempt >= retries:
                logging.warning("阿里云 API 请求失败 [%s.%s]: %s", domain, action, e)
                return None
            time.sleep(1)
    return None


def query_cdt_traffic(user):
    """查询 CDT 公网流量 (GB)。成功返回 float，失败返回 -1"""
    try:
        client = AcsClient(user["ak"].strip(), user["sk"].strip(), "cn-hangzhou")
        data = do_common_request(client, "cdt.aliyuncs.com", "2021-08-13", "ListCdtInternetTraffic", retries=3)
        if data:
            total_bytes = sum(d.get("Traffic", 0) for d in data.get("TrafficDetails", []))
            return total_bytes / (1024 ** 3)
    except Exception as e:
        logging.warning("查询 CDT 流量异常: %s", e)
    return -1


def query_instance_bill(user, instance_id):
    """查询实例当月账单。返回 (金额 float, 货币代码 str)；失败返回 (-1, 'USD')"""
    ak = user["ak"].strip()
    sk = user["sk"].strip()
    bill_endpoint = user.get("bill_endpoint", "business.ap-southeast-1.aliyuncs.com")
    bill_client = AcsClient(ak, sk, billing_api_region(user))
    billing_cycle = datetime.datetime.now().strftime("%Y-%m")

    # 1. 精确查询实例账单
    params = {
        "BillingCycle": billing_cycle,
        "InstanceID": instance_id,
    }
    data = do_common_request(bill_client, bill_endpoint, "2017-12-14", "DescribeInstanceBill", params, retries=3)
    if data and data.get("Success"):
        items = data.get("Data", {}).get("Items", [])
        if items:
            amount = sum(float(item.get("PretaxAmount", 0)) for item in items)
            currency = items[0].get("Currency", "USD")
            return amount, currency

    # 2. 回退查询账单总览
    params2 = {"BillingCycle": billing_cycle}
    data2 = do_common_request(bill_client, bill_endpoint, "2017-12-14", "QueryBillOverview", params2, retries=3)
    if data2:
        items2 = data2.get("Data", {}).get("Items", {}).get("Item", [])
        if items2:
            amount2 = sum(float(item.get("PretaxAmount", 0)) for item in items2)
            currency2 = items2[0].get("Currency", "USD")
            return amount2, currency2

    return -1, user.get("currency", "USD")


def query_account_balance(user):
    """查询账户可用余额。返回 (余额 float 或 None, 货币代码 str 或 None)"""
    ak = user["ak"].strip()
    sk = user["sk"].strip()
    bill_endpoint = user.get("bill_endpoint", "business.ap-southeast-1.aliyuncs.com")
    bill_client = AcsClient(ak, sk, billing_api_region(user))

    endpoints = [bill_endpoint]
    for fallback in ("business.aliyuncs.com", "business.ap-southeast-1.aliyuncs.com"):
        if fallback not in endpoints:
            endpoints.append(fallback)

    for ep in endpoints:
        data = do_common_request(bill_client, ep, "2017-12-14", "QueryAccountBalance", retries=1)
        if data and data.get("Success"):
            d = data.get("Data", {})
            raw_avail = d.get("AvailableAmount", "")
            curr = d.get("Currency", "")
            if raw_avail:
                try:
                    avail_val = float(str(raw_avail).replace(",", ""))
                    return avail_val, curr or user.get("currency", "USD")
                except Exception:
                    pass
    return None, None
