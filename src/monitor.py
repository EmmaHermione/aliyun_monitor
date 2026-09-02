# -*- coding: utf-8 -*-
import datetime
import json
import logging
import os
import sys
import time
import requests
from logging.handlers import RotatingFileHandler
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526.StartInstanceRequest import StartInstanceRequest
from aliyunsdkecs.request.v20140526.StopInstanceRequest import StopInstanceRequest

from common import (
    MONITOR_STATE_FILE,
    load_config,
    load_json,
    query_cdt_traffic,
    query_instance_bill,
    save_json,
)
from ddns import (
    ddns_record_key,
    instance_public_ip,
    is_in_schedule_window,
    parse_hhmm,
    sync_ddns_if_needed,
)

LOG_FILE = "/opt/scripts/monitor.log"
STATE_FILE = MONITOR_STATE_FILE

NOTIFY_COOLDOWN = 3600
OVERLIMIT_COOLDOWN = 86400
START_WAIT_TIMEOUT = 180
START_POLL_INTERVAL = 10
MAX_START_FAILURES = 3
RESOURCE_RETRY_COOLDOWN = 1800
DDNS_HANDOFF_GRACE_SECONDS = 900
DDNS_HANDOFF_LINGER_SECONDS = 240

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)


def load_state():
    state = load_json(STATE_FILE, {})
    # 月度交替时自动清理过期通知状态
    current_month = datetime.datetime.now().strftime("%Y-%m")
    if state.get("__month__") != current_month:
        # 仅保留失败重试计数，清理旧月份的告警冷却
        new_state = {"__month__": current_month}
        for k, v in state.items():
            if isinstance(v, dict) and "start_failures" in v:
                new_state[k] = {"start_failures": v.get("start_failures", 0)}
        state = new_state
        save_state(state)
    return state


def save_state(state):
    save_json(STATE_FILE, state)


def can_notify(state, instance_id, event_key, cooldown=None):
    if cooldown is None:
        cooldown = NOTIFY_COOLDOWN
    last_ts = state.get(instance_id, {}).get(event_key, 0)
    return (time.time() - last_ts) >= cooldown


def mark_notified(state, instance_id, event_key):
    state.setdefault(instance_id, {})[event_key] = time.time()


def get_start_failures(state, instance_id):
    return state.get(instance_id, {}).get("start_failures", 0)


def set_start_failures(state, instance_id, count):
    state.setdefault(instance_id, {})["start_failures"] = count


def reset_start_failures(state, instance_id):
    state.setdefault(instance_id, {})["start_failures"] = 0


def send_tg_alert(tg_conf, title, message, color_status):
    if not tg_conf.get("bot_token") or not tg_conf.get("chat_id"):
        return
    icon = "✅" if color_status == "green" else "🚨"
    try:
        url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
        text = f"{icon} *[{title}]*\n\n{message}"
        data = {"chat_id": tg_conf["chat_id"], "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error("TG发送失败: %s", e)


def get_current_traffic_text(user):
    gb = query_cdt_traffic(user)
    return f"{gb:.2f}GB" if gb >= 0 else "查询失败"


def get_current_traffic_gb(user):
    return query_cdt_traffic(user)


def get_current_bill_info(user):
    instance_id = user.get("instance_id", "").strip()
    amount, curr = query_instance_bill(user, instance_id)
    if amount >= 0:
        return amount, curr
    raise RuntimeError("账单查询失败")


def bill_limit_for_currency(user, bill_currency):
    limit = float(user.get("bill_threshold", 1.0))
    if bill_currency == "CNY":
        return limit * 7.0
    return limit


def format_bill_amount(amount, currency):
    if currency == "CNY":
        return f"¥{amount:.2f}"
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{currency} {amount:.2f}"


def get_instance_status(client, instance_id):
    instance = get_instance(client, instance_id)
    if not instance:
        return None
    return instance.get("Status")


def get_instance(client, instance_id):
    req_ecs = DescribeInstancesRequest()
    req_ecs.set_InstanceIds(json.dumps([instance_id]))
    try:
        resp_ecs = client.do_action_with_exception(req_ecs)
        data_ecs = json.loads(resp_ecs.decode("utf-8"))
        instances = data_ecs.get("Instances", {}).get("Instance", [])
        return instances[0] if instances else None
    except Exception as e:
        logger.error(f"查询实例详情失败 [{instance_id}]: {e}")
        return None


def start_instance(client, instance_id):
    start_req = StartInstanceRequest()
    start_req.set_InstanceId(instance_id)
    client.do_action_with_exception(start_req)


def stop_instance_in_saving_mode(client, instance_id):
    stop_req = StopInstanceRequest()
    stop_req.set_InstanceId(instance_id)
    stop_req.set_StoppedMode("StopCharging")
    client.do_action_with_exception(stop_req)


def schedule_desc(user):
    if not user.get("schedule_enabled"):
        return "全天运行"
    return f"{user.get('schedule_start', '00:00')}-{user.get('schedule_end', '23:59')}"


def ddns_ready(user, public_ip, ddns_result):
    if not ddns_record_key(user):
        return True
    return bool(public_ip) and (ddns_result is None or bool(ddns_result.get("ok")))


def handoff_grace_remaining(state, instance_id):
    item = state.setdefault(instance_id, {})
    start_ts = item.get("ddns_handoff_since")
    now = time.time()
    if not start_ts:
        item["ddns_handoff_since"] = now
        return DDNS_HANDOFF_GRACE_SECONDS
    elapsed = now - float(start_ts)
    return max(0, int(DDNS_HANDOFF_GRACE_SECONDS - elapsed))


def clear_handoff_grace(state, instance_id):
    item = state.setdefault(instance_id, {})
    item.pop("ddns_handoff_since", None)
    item.pop("ddns_linger_since", None)


def handoff_linger_remaining(state, instance_id):
    item = state.setdefault(instance_id, {})
    start_ts = item.get("ddns_linger_since")
    now = time.time()
    if not start_ts:
        item["ddns_linger_since"] = now
        return DDNS_HANDOFF_LINGER_SECONDS
    elapsed = now - float(start_ts)
    return max(0, int(DDNS_HANDOFF_LINGER_SECONDS - elapsed))


def stop_for_schedule(client, user, tg_conf, state, result):
    instance_id = user["instance_id"]
    name = user.get("name", instance_id)
    logger.info(f"[{name}] 当前不在计划运行时段({schedule_desc(user)})，正在节省停机实例...")
    stop_instance_in_saving_mode(client, instance_id)
    result["status"] = "Stopping"
    clear_handoff_grace(state, instance_id)
    if can_notify(state, instance_id, "schedule_stopped"):
        msg = (
            f"机器: {name}\n"
            f"计划时段: {schedule_desc(user)}\n"
            f"当前流量: {get_current_traffic_text(user)}\n"
            f"动作: 已按定时计划节省停机"
        )
        send_tg_alert(tg_conf, "定时计划", msg, "green")
        mark_notified(state, instance_id, "schedule_stopped")


def check_and_act(user, tg_conf, state, allow_schedule_stop=True, config=None):
    instance_id = user["instance_id"]
    name = user.get("name", instance_id)
    in_window = is_in_schedule_window(user)
    manual_override = user.get("manual_override")
    result = {
        "instance_id": instance_id,
        "name": name,
        "in_window": in_window,
        "status": "Unknown",
        "ddns_record": ddns_record_key(user),
        "ddns_ready": False,
    }
    if user.get("paused") or user.get("disabled"):
        logger.info(f"[{name}] 监控已暂停，跳过本轮检查")
        result["status"] = "Paused"
        return result
    try:
        client = AcsClient(user["ak"], user["sk"], user["region"])

        # 1. 获取实例当前状态，先执行计划窗口判断，避免流量查询失败影响定时计划节省停机
        instance = get_instance(client, instance_id)
        if instance is None:
            logger.error(f"[{name}] 未找到实例: {instance_id}")
            result["status"] = "NotFound"
            return result
        status = instance.get("Status")
        result["status"] = status

        if not in_window and manual_override != "run":
            if status == "Running":
                if not allow_schedule_stop:
                    logger.info(f"[{name}] 不在计划时段，暂缓停机等待新当班实例启动与 DDNS 同步...")
                    return result
                stop_for_schedule(client, user, tg_conf, state, result)
            else:
                clear_handoff_grace(state, instance_id)
                logger.info(f"[{name}] 当前不在计划时段({schedule_desc(user)})，实例已处于 {status} 状态")
            return result

        clear_handoff_grace(state, instance_id)

        # 2. 查询 CDT 流量
        try:
            curr_gb = get_current_traffic_gb(user)
            if curr_gb < 0:
                raise RuntimeError("CDT 接口返回为空")
        except Exception as e:
            logger.warning(f"[{name}] 查询流量失败: {e}，跳过开机与止损判断")
            if can_notify(state, instance_id, "traffic_query_error"):
                send_tg_alert(tg_conf, "监控异常", f"机器: {name}\n原因: 查询流量接口失败: {e}", "red")
                mark_notified(state, instance_id, "traffic_query_error")
            return result

        # 3. 账单止损检查
        try:
            bill_amount, bill_currency = get_current_bill_info(user)
            bill_limit = bill_limit_for_currency(user, bill_currency)
            if bill_amount >= bill_limit:
                bill_text = format_bill_amount(bill_amount, bill_currency)
                bill_limit_text = format_bill_amount(bill_limit, bill_currency)
                logger.warning(f"[{name}] 账单超标({bill_text} >= {bill_limit_text})，触发节省停机止损")
                if status == "Running":
                    stop_instance_in_saving_mode(client, instance_id)
                    result["status"] = "Stopping"
                if can_notify(state, instance_id, "bill_overlimit", OVERLIMIT_COOLDOWN):
                    msg = (
                        f"机器: {name}\n"
                        f"当前账单: {bill_text}\n"
                        f"账单阈值: {bill_limit_text}\n"
                        f"当前状态: {status}\n"
                        f"动作: 已触发账单超标节省停机止损"
                    )
                    send_tg_alert(tg_conf, "账单扣费预警", msg, "red")
                    mark_notified(state, instance_id, "bill_overlimit")
                return result
        except Exception as e:
            logger.warning(f"[{name}] 查询账单失败: {e}，跳过账单止损")
            if can_notify(state, instance_id, "bill_query_error"):
                send_tg_alert(tg_conf, "账单查询异常", f"机器: {name}\n原因: 查询账单接口失败: {e}", "red")
                mark_notified(state, instance_id, "bill_query_error")

        # 4. 手动覆盖保持停机逻辑
        if manual_override == "stop":
            if status == "Running":
                logger.info(f"[{name}] 手动覆盖保持停机，正在节省停机...")
                stop_instance_in_saving_mode(client, instance_id)
                result["status"] = "Stopping"
                if can_notify(state, instance_id, "manual_override_stop"):
                    msg = f"机器: {name}\n动作: 手动覆盖保持停机，已执行节省停机"
                    send_tg_alert(tg_conf, "手动覆盖", msg, "green")
                    mark_notified(state, instance_id, "manual_override_stop")
            else:
                logger.info(f"[{name}] 手动覆盖保持停机，实例状态: {status}")
            return result

        limit = user.get("traffic_limit", 180)
        if curr_gb < limit:
            # ---- 流量安全 ----
            if status == "Stopped":
                failures = get_start_failures(state, instance_id)

                # 连续失败过多，进入慢重试降频保护
                if failures >= MAX_START_FAILURES:
                    last_ts = state.get(instance_id, {}).get("last_retry_ts", 0)
                    elapsed = time.time() - last_ts
                    if elapsed < RESOURCE_RETRY_COOLDOWN:
                        wait_min = int((RESOURCE_RETRY_COOLDOWN - elapsed) / 60)
                        logger.warning(f"[{name}] 启动连续失败已达 {failures} 次，冷却中，约 {wait_min} 分钟后重试")
                        return result
                    logger.info(f"[{name}] 冷却期已过，重新尝试启动实例...")
                    state.setdefault(instance_id, {})["last_retry_ts"] = time.time()

                logger.info(f"[{name}] 流量安全({curr_gb:.2f}GB < {limit}GB)，处于计划时段，尝试启动实例...")
                try:
                    start_instance(client, instance_id)
                except Exception as e:
                    err_msg = str(e)
                    new_failures = failures + 1
                    set_start_failures(state, instance_id, new_failures)
                    logger.warning(f"[{name}] StartInstance API 调用失败: {err_msg}，累计失败 {new_failures} 次")
                    if can_notify(state, instance_id, "start_failed"):
                        msg = (f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n"
                               f"⚠️ 启动 API 调用失败: {err_msg}\n"
                               f"累计失败 {new_failures} 次，"
                               f"脚本将每 {RESOURCE_RETRY_COOLDOWN//60} 分钟自动重试。")
                        send_tg_alert(tg_conf, "启动失败告警", msg, "red")
                        mark_notified(state, instance_id, "start_failed")
                    return result

                # --- 轮询等待实例真正进入 Running 状态 ---
                started = False
                waited = 0
                while waited < START_WAIT_TIMEOUT:
                    time.sleep(START_POLL_INTERVAL)
                    waited += START_POLL_INTERVAL
                    try:
                        real_status = get_instance_status(client, instance_id)
                    except Exception:
                        real_status = "Unknown"
                    logger.info(f"[{name}] 等待启动... 当前状态: {real_status} ({waited}s)")
                    if real_status == "Running":
                        started = True
                        break
                    elif real_status == "Stopped":
                        logger.warning(f"[{name}] 实例已回落到 Stopped 状态，启动被拒绝")
                        break

                if started:
                    try:
                        instance = get_instance(client, instance_id)
                    except Exception:
                        instance = None
                    public_ip = instance_public_ip(instance)
                    ddns_result = sync_ddns_if_needed(user, state, instance_id, public_ip, force=True, logger=logger, config=config)
                    ddns_line = f"\n{ddns_result['message']}" if ddns_result else ""
                    result["status"] = "Running"
                    result["public_ip"] = public_ip
                    result["ddns_ready"] = ddns_ready(user, public_ip, ddns_result)

                    reset_start_failures(state, instance_id)
                    state.setdefault(instance_id, {}).pop("no_resource", None)
                    state.setdefault(instance_id, {}).pop("last_retry_ts", None)
                    logger.info(f"[{name}] 实例已恢复运行")
                    if can_notify(state, instance_id, "resumed"):
                        msg = (
                            f"机器: {name}\n"
                            f"计划时段: {schedule_desc(user)}\n"
                            f"当前流量: {curr_gb:.2f}GB\n"
                            f"公网 IP: {public_ip or '无公网IP'}{ddns_line}\n"
                            f"动作: 恢复运行"
                        )
                        send_tg_alert(tg_conf, "恢复监控", msg, "green")
                        mark_notified(state, instance_id, "resumed")
                else:
                    new_failures = failures + 1
                    set_start_failures(state, instance_id, new_failures)
                    logger.warning(f"[{name}] 启动超时或被拒绝，累计失败 {new_failures} 次")
                    if can_notify(state, instance_id, "start_failed"):
                        msg = (f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n"
                               f"⚠️ 尝试启动但 {START_WAIT_TIMEOUT}s 内未变为 Running 状态，"
                               f"累计失败 {new_failures} 次。\n"
                               f"脚本将每 {RESOURCE_RETRY_COOLDOWN//60} 分钟自动重试，无需手动干预。")
                        send_tg_alert(tg_conf, "启动失败告警", msg, "red")
                        mark_notified(state, instance_id, "start_failed")

            elif status == "Running":
                reset_start_failures(state, instance_id)
                public_ip = instance_public_ip(instance)
                ddns_result = sync_ddns_if_needed(user, state, instance_id, public_ip, logger=logger, config=config)
                if ddns_result:
                    logger.info(f"[{name}] {ddns_result['message']}")
                result["public_ip"] = public_ip
                result["ddns_ready"] = ddns_ready(user, public_ip, ddns_result)
                logger.info(f"[{name}] 流量安全({curr_gb:.2f}GB)，实例运行中")
            else:
                logger.info(f"[{name}] 实例处于中间态: {status}，不干预")

        else:
            # ---- 流量超标 ----
            if status == "Running":
                logger.info(f"[{name}] 流量超标({curr_gb:.2f}GB >= {limit}GB)，正在节省停机...")
                stop_instance_in_saving_mode(client, instance_id)
                result["status"] = "Stopping"
                if can_notify(state, instance_id, "overlimit", OVERLIMIT_COOLDOWN):
                    msg = f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n动作: 已触发流量止损节省停机"
                    send_tg_alert(tg_conf, "流量预警", msg, "red")
                    mark_notified(state, instance_id, "overlimit")
            else:
                logger.info(f"[{name}] 已停止止损 - {curr_gb:.2f}GB")
                if can_notify(state, instance_id, "overlimit", OVERLIMIT_COOLDOWN):
                    msg = f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n状态: 持续节省停机止损中"
                    send_tg_alert(tg_conf, "持续止损提醒", msg, "red")
                    mark_notified(state, instance_id, "overlimit")

    except Exception as e:
        logger.error(f"[{name}] 处理异常: {e}")
        if can_notify(state, instance_id, "general_error"):
            send_tg_alert(tg_conf, "运行异常", f"机器: {name}\n异常详情: {e}", "red")
            mark_notified(state, instance_id, "general_error")

    return result


def main():
    config = load_config()
    tg_conf = config.get("telegram", {})
    state = load_state()
    users = config.get("users", [])
    if not users:
        logger.info("未配置任何用户/实例，退出")
        return

    now = datetime.datetime.now()
    logger.info(f"=== 开始巡检 (共 {len(users)} 个实例) {now.strftime('%Y-%m-%d %H:%M:%S')} ===")

    incoming_records = set()
    for user in users:
        if not (user.get("paused") or user.get("disabled")) and is_in_schedule_window(user):
            rec = ddns_record_key(user)
            if rec:
                incoming_records.add(rec)

    results = []
    for user in users:
        rec = ddns_record_key(user)
        in_window = is_in_schedule_window(user)
        allow_stop = True
        if rec and rec in incoming_records and not in_window and user.get("manual_override") != "run":
            allow_stop = False
        res = check_and_act(user, tg_conf, state, allow_schedule_stop=allow_stop, config=config)
        results.append((user, res))

    ready_records = {
        res["ddns_record"]
        for _, res in results
        if res.get("ddns_record") and res.get("in_window") and res.get("ddns_ready")
    }

    for user, res in results:
        if user.get("paused") or user.get("disabled"):
            continue
        if is_in_schedule_window(user) or user.get("manual_override") == "run":
            continue
        rec = ddns_record_key(user)
        if not rec or rec not in incoming_records:
            continue

        instance_id = user["instance_id"]
        client = AcsClient(user["ak"], user["sk"], user["region"])
        current_status = get_instance_status(client, instance_id) or res.get("status")
        if current_status != "Running":
            clear_handoff_grace(state, instance_id)
            continue

        if rec in ready_records:
            linger_left = handoff_linger_remaining(state, instance_id)
            if linger_left > 0:
                logger.info(f"[{user.get('name', instance_id)}] 新当班实例 DDNS 已就绪，保持运行缓冲等待 DNS 生效，剩余 {linger_left}s")
                continue
            logger.info(f"[{user.get('name', instance_id)}] 新当班实例 DDNS 已就绪且 DNS 缓冲已结束，执行原定节省停机")
            stop_for_schedule(client, user, tg_conf, state, res)
            continue

        grace_left = handoff_grace_remaining(state, instance_id)
        if grace_left > 0:
            logger.info(f"[{user.get('name', instance_id)}] 同域名新当班实例尚未就绪，暂缓节省停机，宽限期剩余 {grace_left}s")
            continue

        logger.warning(f"[{user.get('name', instance_id)}] 达到 DDNS 换班保护上限({DDNS_HANDOFF_GRACE_SECONDS}s)，执行原定节省停机")
        stop_for_schedule(client, user, tg_conf, state, res)

    save_state(state)
    logger.info("=== 巡检完成 ===")


if __name__ == "__main__":
    main()
