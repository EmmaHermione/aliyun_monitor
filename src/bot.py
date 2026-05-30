# -*- coding: utf-8 -*-
import datetime
import json
import logging
import os
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526.RebootInstanceRequest import RebootInstanceRequest
from aliyunsdkecs.request.v20140526.StartInstanceRequest import StartInstanceRequest
from aliyunsdkecs.request.v20140526.StopInstanceRequest import StopInstanceRequest

try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_
    ssl_.HAS_SNI = True
except Exception:
    pass

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_res = [r for r in res if r[0] == socket.AF_INET]
    return ipv4_res if ipv4_res else res


socket.getaddrinfo = _getaddrinfo_ipv4_only

CONFIG_FILE = "/opt/scripts/config.json"
STATE_FILE = "/opt/scripts/bot_state.json"
LOG_FILE = "/opt/scripts/bot.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

TASK_EXECUTOR = ThreadPoolExecutor(max_workers=2)
ACTIVE_TASKS = 0
ACTIVE_TASKS_LOCK = threading.Lock()
PROGRESS_TIMEOUT_SECONDS = 20


def submit_task(config, chat_id, func, *args):
    global ACTIVE_TASKS
    with ACTIVE_TASKS_LOCK:
        if ACTIVE_TASKS >= 2:
            send_message(config, chat_id, "已有查询或操作正在执行，请稍后再试。")
            return
        ACTIVE_TASKS += 1

    def runner():
        global ACTIVE_TASKS
        try:
            func(*args)
        except Exception as e:
            logging.exception("后台任务执行失败")
            send_message(config, chat_id, f"任务执行失败: {e}")
        finally:
            with ACTIVE_TASKS_LOCK:
                ACTIVE_TASKS -= 1

    TASK_EXECUTOR.submit(runner)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error("读取 %s 失败: %s", path, e)
        return default


def save_json(path, data):
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config():
    return load_json(CONFIG_FILE, {})


def save_config(config):
    save_json(CONFIG_FILE, config)


def load_state():
    return load_json(STATE_FILE, {"offset": 0})


def save_state(state):
    save_json(STATE_FILE, state)


def tg_conf(config):
    return config.get("telegram", {})


def api_url(config, method):
    return f"https://api.telegram.org/bot{tg_conf(config).get('bot_token', '')}/{method}"


def tg_request(config, method, payload=None, timeout=20):
    try:
        resp = requests.post(api_url(config, method), json=payload or {}, timeout=timeout)
        data = resp.json()
        if not data.get("ok"):
            description = str(data.get("description", ""))
            if method == "answerCallbackQuery" and "query is too old" in description:
                logging.info("Telegram 按钮回调已过期，忽略: %s", description)
            else:
                logging.warning("Telegram API %s 失败: %s", method, data)
        return data
    except Exception as e:
        logging.error("Telegram API %s 异常: %s", method, e)
        return {"ok": False}


def send_message(config, chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return tg_request(config, "sendMessage", payload)


def edit_message(config, chat_id, message_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return tg_request(config, "editMessageText", payload)


def delete_message(config, chat_id, message_id):
    return tg_request(config, "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=10)


def begin_progress(config, chat_id, text, timeout_text=None, timeout_seconds=PROGRESS_TIMEOUT_SECONDS):
    response = send_message(config, chat_id, text)
    message_id = (response.get("result") or {}).get("message_id") if response.get("ok") else None
    timed_out = threading.Event()
    done = threading.Event()

    def on_timeout():
        if done.is_set() or not message_id:
            return
        timed_out.set()
        edit_message(
            config,
            chat_id,
            message_id,
            timeout_text or "查询仍未返回，可能是阿里云接口或服务器网络超时，请稍后再试。",
        )

    timer = threading.Timer(timeout_seconds, on_timeout)
    timer.daemon = True
    timer.start()

    def finish():
        done.set()
        timer.cancel()
        if message_id:
            delete_message(config, chat_id, message_id)

    return finish


def answer_callback(config, callback_id, text=""):
    tg_request(config, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}, timeout=10)


def is_allowed(config, chat_id):
    allowed = str(tg_conf(config).get("chat_id", "")).strip()
    return allowed and str(chat_id) == allowed


def users(config):
    return config.get("users", [])


def user_label(user, index=None):
    name = user.get("name") or user.get("instance_id") or "Unknown"
    return f"{index + 1}. {name}" if index is not None else name


def find_user(config, key):
    key = str(key or "").strip()
    if not key:
        return None, None
    for idx, user in enumerate(users(config)):
        if key == str(idx + 1) or key == user.get("name") or key == user.get("instance_id"):
            return idx, user
    return None, None


def client_for(user):
    return AcsClient(user["ak"], user["sk"], user["region"])


def get_status(user):
    req = DescribeInstancesRequest()
    req.set_InstanceIds(json.dumps([user["instance_id"]]))
    resp = client_for(user).do_action_with_exception(req)
    data = json.loads(resp.decode("utf-8"))
    instances = data.get("Instances", {}).get("Instance", [])
    if not instances:
        return "NotFound", None
    return instances[0].get("Status", "Unknown"), instances[0]


def status_icon_for_mode(status, stopped_mode):
    mode = str(stopped_mode or '').strip()
    if status == 'Running':
        return '🟢'
    if status == 'Stopped':
        return '🔴' if mode == 'KeepCharging' else '⚫'
    if status == 'NotFound':
        return '❓'
    return '🔴'

def schedule_text(user):
    if not user.get("schedule_enabled"):
        return "全天运行"
    return f"{user.get('schedule_start', '00:00')}-{user.get('schedule_end', '23:59')}"


def instance_status_text(user):
    status, inst = get_status(user)
    ip = "N/A"
    spec = "N/A"
    stopped_mode = ""
    if inst:
        public_ips = inst.get("PublicIpAddress", {}).get("IpAddress", [])
        eip = inst.get("EipAddress", {}).get("IpAddress", "")
        ip = eip or (public_ips[0] if public_ips else "无公网IP")
        mem_gb = inst.get("Memory", 0) / 1024
        spec = f"{inst.get('Cpu', 0)}C{mem_gb:g}G"
        stopped_mode = inst.get("StoppedMode", "")
    return (
        f"📊 {user_label(user)}\n"
        f"实例: {user.get('instance_id')}\n"
        f"区域: {user.get('region')}\n"
        f"状态: {status_icon_for_mode(status, stopped_mode)} {status}\n"
        f"规格: {spec}\n"
        f"IP: {ip}\n"
        f"计划: {schedule_text(user)}"
    )


def start_instance(user):
    req = StartInstanceRequest()
    req.set_InstanceId(user["instance_id"])
    client_for(user).do_action_with_exception(req)


def stop_instance(user):
    req = StopInstanceRequest()
    req.set_InstanceId(user["instance_id"])
    req.set_StoppedMode("StopCharging")
    client_for(user).do_action_with_exception(req)


def reboot_instance(user):
    req = RebootInstanceRequest()
    req.set_InstanceId(user["instance_id"])
    client_for(user).do_action_with_exception(req)


def normalize_hhmm(value):
    try:
        hour, minute = str(value).strip().split(":", 1)
        hour = int(hour)
        minute = int(minute)
        if hour == 24 and minute == 0:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except Exception:
        pass
    return None


def is_hhmm(value):
    return normalize_hhmm(value) is not None


def main_keyboard(config):
    rows = [[{"text": user_label(user, idx), "callback_data": f"select:{idx}"}] for idx, user in enumerate(users(config))]
    rows.append([{"text": "📊 获取报告", "callback_data": "report"}])
    rows.append([{"text": "🔄 刷新列表", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def instance_keyboard(index):
    return {
        "inline_keyboard": [
            [
                {"text": "🟢 开机", "callback_data": f"act:start:{index}"},
                {"text": "🔴 节省停机", "callback_data": f"act:stop:{index}"},
            ],
            [
                {"text": "🔁 重启", "callback_data": f"act:reboot:{index}"},
                {"text": "✏️ 修改定时", "callback_data": f"sched_edit:{index}"},
            ],
            [
                {"text": "🗑 删除定时", "callback_data": f"unschedule:{index}"},
                {"text": "⏸️ 暂停/恢复监控", "callback_data": f"toggle_pause:{index}"},
            ],
            [{"text": "⬅️ 返回", "callback_data": "menu"}],
        ]
    }


HELP_TEXT = """可用命令:
/menu - 打开实例管理菜单
/list - 查看实例列表
/report - 获取当前日报内容
/status 机器名或序号 - 查询状态
/start 机器名或序号 - 开机
/stop 机器名或序号 - 节省停机
/reboot 机器名或序号 - 重启
/schedule 机器名或序号 HH:MM HH:MM - 设置每日运行窗口
/unschedule 机器名或序号 - 删除定时窗口

示例:
/schedule HK001 01:00 13:00
/unschedule HK001"""


def show_menu(config, chat_id, message_id=None):
    text = "请选择要管理的 ECS 实例："
    if message_id:
        edit_message(config, chat_id, message_id, text, main_keyboard(config))
    else:
        send_message(config, chat_id, text, main_keyboard(config))


def show_instance(config, chat_id, index, message_id=None):
    all_users = users(config)
    if index < 0 or index >= len(all_users):
        send_message(config, chat_id, "实例不存在，请重新打开 /menu。")
        return
    user = all_users[index]
    finish_progress = begin_progress(
        config,
        chat_id,
        f"正在查询 {user_label(user)}，请稍候...",
        f"{user_label(user)} 查询仍未返回，可能是阿里云接口或服务器网络超时，请稍后查看结果。",
    )
    try:
        from report import build_user_report
        detail = build_user_report(user)
    except Exception as e:
        logging.exception("查询实例状态失败")
        detail = f"📊 {user_label(user)}\n状态查询失败: {e}"
    text = f"✅ 已选: {user_label(user)}\n\n{detail}\n\n请选择操作："
    try:
        if message_id:
            edit_message(config, chat_id, message_id, text, instance_keyboard(index), parse_mode="Markdown")
        else:
            send_message(config, chat_id, text, instance_keyboard(index), parse_mode="Markdown")
    finally:
        finish_progress()


def run_action(config, chat_id, action, index):
    all_users = users(config)
    if index < 0 or index >= len(all_users):
        send_message(config, chat_id, "实例不存在，请重新打开 /menu。")
        return
    user = all_users[index]
    name = user_label(user)
    action_name = {"start": "开机", "stop": "节省停机", "reboot": "重启", "status": "查询"}.get(action, action)
    finish_progress = begin_progress(
        config,
        chat_id,
        f"正在{action_name}: {name}",
        f"{name} 的{action_name}操作仍未返回，可能是阿里云接口或服务器网络超时，请稍后查看结果。",
    )
    try:
        if action == "start":
            start_instance(user)
            send_message(config, chat_id, f"🟢 已发送开机指令: {name}")
        elif action == "stop":
            stop_instance(user)
            send_message(config, chat_id, f"🔴 已发送节省停机指令: {name}")
        elif action == "reboot":
            reboot_instance(user)
            send_message(config, chat_id, f"🔁 已发送重启指令: {name}")
        elif action == "status":
            from report import build_user_report
            send_message(config, chat_id, build_user_report(user), parse_mode="Markdown")
    except Exception as e:
        logging.exception("执行 %s 失败", action)
        send_message(config, chat_id, f"❌ {name} 操作失败: {e}")
    finally:
        finish_progress()


def set_schedule(config, chat_id, key, start, end):
    idx, user = find_user(config, key)
    if user is None:
        send_message(config, chat_id, f"未找到实例: {key}")
        return
    start = normalize_hhmm(start)
    end = normalize_hhmm(end)
    if start is None or end is None:
        send_message(config, chat_id, "时间格式错误，请使用 HH:MM，例如 /schedule HK001 01:00 13:00")
        return
    user["schedule_enabled"] = True
    user["schedule_start"] = start
    user["schedule_end"] = end
    save_config(config)
    send_message(config, chat_id, f"✅ [定时计划]\n\n机器: {user_label(user)}\n计划时段: {schedule_text(user)}\n动作: 已更新定时计划")


def clear_schedule(config, chat_id, key):
    idx, user = find_user(config, key)
    if user is None:
        send_message(config, chat_id, f"未找到实例: {key}")
        return
    user["schedule_enabled"] = False
    save_config(config)
    send_message(config, chat_id, f"✅ [定时计划]\n\n机器: {user_label(user)}\n动作: 已删除定时计划，恢复全天运行")


def toggle_pause(config, chat_id, key):
    idx, user = find_user(config, key)
    if user is None:
        send_message(config, chat_id, f"未找到实例: {key}")
        return
    paused = bool(user.get("paused") or user.get("disabled"))
    user["paused"] = not paused
    user.pop("disabled", None)
    save_config(config)
    state = "已暂停" if user["paused"] else "已恢复"
    title = "暂停监控" if user["paused"] else "恢复监控"
    send_message(config, chat_id, f"✅ [{title}]\n\n机器: {user_label(user)}\n动作: 监控{state}")


def pending_key(chat_id):
    return str(chat_id)


def pending_schedules(state):
    return state.setdefault("pending_schedules", {})


def set_pending_schedule(state, chat_id, index):
    pending_schedules(state)[pending_key(chat_id)] = index


def pop_pending_schedule(state, chat_id):
    return pending_schedules(state).pop(pending_key(chat_id), None)


def handle_pending_schedule(config, state, chat_id, text):
    key = pending_key(chat_id)
    idx = pending_schedules(state).get(key)
    if idx is None:
        return False
    parts = text.strip().split()
    if len(parts) < 2 or not is_hhmm(parts[0]) or not is_hhmm(parts[1]):
        send_message(config, chat_id, "时间格式错误，请直接重新输入开始和结束时间，格式：HH:MM HH:MM")
        return True
    pop_pending_schedule(state, chat_id)
    set_schedule(config, chat_id, str(idx + 1), parts[0], parts[1])
    return True


def send_report(config, chat_id):
    finish_progress = begin_progress(
        config,
        chat_id,
        "正在生成日报，请稍候...",
        "日报生成仍未返回，可能是阿里云 CDT/账单接口或服务器网络超时，请稍后查看结果。",
    )
    try:
        from report import build_report
        send_message(config, chat_id, build_report(config), parse_mode="Markdown")
    except Exception as e:
        logging.exception("生成报告失败")
        send_message(config, chat_id, f"❌ 生成报告失败: {e}")
    finally:
        finish_progress()


def handle_command(config, chat_id, text, state=None):
    parts = text.strip().split()
    command = parts[0].split("@", 1)[0].lower()
    if command == "/start" and len(parts) >= 2:
        idx, user = find_user(config, parts[1])
        if user is None:
            send_message(config, chat_id, f"未找到实例: {parts[1]}")
            return
        submit_task(config, chat_id, run_action, config, chat_id, "start", idx)
    elif command in ("/start", "/menu"):
        show_menu(config, chat_id)
    elif command == "/help":
        send_message(config, chat_id, HELP_TEXT)
    elif command == "/list":
        lines = ["ECS 实例列表："]
        for idx, user in enumerate(users(config)):
            lines.append(f"{idx + 1}. {user_label(user)} | {user.get('instance_id')} | {schedule_text(user)}")
        send_message(config, chat_id, "\n".join(lines))
    elif command == "/report":
        submit_task(config, chat_id, send_report, config, chat_id)
    elif command in ("/status", "/stop", "/reboot") and len(parts) >= 2:
        idx, user = find_user(config, parts[1])
        if user is None:
            send_message(config, chat_id, f"未找到实例: {parts[1]}")
            return
        action = command.lstrip("/")
        submit_task(config, chat_id, run_action, config, chat_id, action, idx)
    elif command == "/schedule" and len(parts) >= 4:
        set_schedule(config, chat_id, parts[1], parts[2], parts[3])
    elif command in ("/unschedule", "/delschedule") and len(parts) >= 2:
        clear_schedule(config, chat_id, parts[1])
    else:
        send_message(config, chat_id, HELP_TEXT)


def handle_callback(config, callback, state):
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    data = callback.get("data", "")

    if not is_allowed(config, chat_id):
        answer_callback(config, callback_id, "无权限")
        return

    answer_callback(config, callback_id)
    if data == "menu":
        show_menu(config, chat_id, message_id)
    elif data == "report":
        submit_task(config, chat_id, send_report, config, chat_id)
    elif data.startswith("select:"):
        submit_task(config, chat_id, show_instance, config, chat_id, int(data.split(":", 1)[1]), message_id)
    elif data.startswith("act:"):
        _, action, idx = data.split(":", 2)
        submit_task(config, chat_id, run_action, config, chat_id, action, int(idx))
    elif data.startswith("sched_edit:"):
        idx = int(data.split(":", 1)[1])
        all_users = users(config)
        if 0 <= idx < len(all_users):
            name = user_label(all_users[idx])
            set_pending_schedule(state, chat_id, idx)
            send_message(config, chat_id, f"正在修改 {name} 的定时。\n请发送开始时间和结束时间，格式：HH:MM HH:MM")
    elif data.startswith("unschedule:"):
        idx = int(data.split(":", 1)[1])
        all_users = users(config)
        if 0 <= idx < len(all_users):
            clear_schedule(config, chat_id, str(idx + 1))
    elif data.startswith("toggle_pause:"):
        idx = int(data.split(":", 1)[1])
        all_users = users(config)
        if 0 <= idx < len(all_users):
            toggle_pause(config, chat_id, str(idx + 1))
            submit_task(config, chat_id, show_instance, load_config(), chat_id, idx, message_id)


def handle_update(config, update, state):
    if "callback_query" in update:
        handle_callback(config, update["callback_query"], state)
        return

    message = update.get("message") or update.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    if not text:
        return
    if not is_allowed(config, chat_id):
        logging.warning("拒绝未授权 chat_id=%s", chat_id)
        send_message(config, chat_id, "无权限。")
        return
    if not text.strip().startswith("/") and handle_pending_schedule(config, state, chat_id, text):
        return
    handle_command(config, chat_id, text, state)


def poll_once(config, state):
    payload = {"offset": state.get("offset", 0), "timeout": 25, "allowed_updates": ["message", "callback_query"]}
    data = tg_request(config, "getUpdates", payload, timeout=35)
    if not data.get("ok"):
        time.sleep(5)
        return
    updates = data.get("result", [])
    if updates:
        logging.info("收到 %s 条 Telegram 更新", len(updates))
    for update in updates:
        state["offset"] = update["update_id"] + 1
        handle_update(load_config(), update, state)
    save_state(state)


def main():
    logging.info("Telegram Bot 服务启动")
    while True:
        config = load_config()
        if not tg_conf(config).get("bot_token") or not tg_conf(config).get("chat_id"):
            logging.error("Telegram 配置缺失，10 秒后重试")
            time.sleep(10)
            continue
        state = load_state()
        try:
            poll_once(config, state)
        except Exception as e:
            logging.exception("Bot 主循环异常: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
