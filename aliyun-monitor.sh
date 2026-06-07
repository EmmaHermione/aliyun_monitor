#!/bin/bash

# 定义颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO_BRANCH="${ALIYUN_MONITOR_BRANCH:-main}"
REPO_BASE_URL="https://raw.githubusercontent.com/EmmaHermione/aliyun_monitor/refs/heads/${REPO_BRANCH}"
REPO_URL="${REPO_BASE_URL}/src"
INSTALLER_NAME="aliyun-monitor.sh"
INSTALL_URL="${REPO_BASE_URL}/${INSTALLER_NAME}"
UNINSTALL_URL="${REPO_BASE_URL}/uninstall.sh"
TARGET_DIR="/opt/scripts"
VENV_DIR="${TARGET_DIR}/venv"
CONFIG_FILE="${TARGET_DIR}/config.json"
BOT_SERVICE="aliyun-monitor-bot.service"

# 全局变量，用于在函数间传递生成的 JSON 数据
CURRENT_USER_JSON=""

function normalize_hhmm() {
    local VALUE="$1"
    local HOUR=""
    local MINUTE=""

    if [[ "$VALUE" != *:* ]]; then
        return 1
    fi

    HOUR="${VALUE%%:*}"
    MINUTE="${VALUE#*:}"

    if [[ "$HOUR" == "$VALUE" || "$MINUTE" == *:* ]]; then
        return 1
    fi
    if [[ ! "$HOUR" =~ ^[0-9][0-9]?$ || ! "$MINUTE" =~ ^[0-9][0-9]?$ ]]; then
        return 1
    fi

    HOUR=$((10#$HOUR))
    MINUTE=$((10#$MINUTE))

    if [ "$HOUR" -eq 24 ] && [ "$MINUTE" -eq 0 ]; then
        HOUR=0
    fi

    if [ "$HOUR" -ge 0 ] && [ "$HOUR" -le 23 ] && [ "$MINUTE" -ge 0 ] && [ "$MINUTE" -le 59 ]; then
        printf '%02d:%02d\n' "$HOUR" "$MINUTE"
        return 0
    fi

    return 1
}

function is_hhmm() {
    normalize_hhmm "$1" >/dev/null
}

function prompt_hhmm() {
    local VAR_NAME="$1"
    local PROMPT_TEXT="$2"
    local DEFAULT_VALUE="$3"
    local INPUT_VALUE=""

    while true; do
        read -p "$PROMPT_TEXT" INPUT_VALUE
        INPUT_VALUE=${INPUT_VALUE:-$DEFAULT_VALUE}
        local NORMALIZED_VALUE=""
        if NORMALIZED_VALUE=$(normalize_hhmm "$INPUT_VALUE"); then
            printf -v "$VAR_NAME" "%s" "$NORMALIZED_VALUE"
            return
        fi
        echo -e "${RED}时间格式无效，请输入 HH:MM，例如 09:00、12:30、00:00；也支持 0:00 或 24:00 自动转为 00:00${NC}"
    done
}

function install_script_file() {
    local FILE_NAME="$1"
    local TARGET_PATH="${TARGET_DIR}/${FILE_NAME}"

    if [ -s "$TARGET_PATH" ]; then
        echo -e "${GREEN}✓ 使用服务器本地文件: ${TARGET_PATH}${NC}"
        return 0
    fi

    echo -e "${YELLOW}${TARGET_PATH} 不存在，从 GitHub 下载 ${FILE_NAME}...${NC}"
    if wget -q -O "${TARGET_PATH}.tmp" "${REPO_URL}/${FILE_NAME}" && [ -s "${TARGET_PATH}.tmp" ]; then
        mv "${TARGET_PATH}.tmp" "$TARGET_PATH"
        echo -e "${GREEN}✓ 已下载: ${FILE_NAME}${NC}"
        return 0
    fi

    rm -f "${TARGET_PATH}.tmp"
    echo -e "${RED}${FILE_NAME} 获取失败，请检查 /opt/scripts 文件或 GitHub 网络。${NC}"
    return 1
}

function download_script_file() {
    local FILE_NAME="$1"
    local TARGET_PATH="${TARGET_DIR}/${FILE_NAME}"

    echo -e "${YELLOW}从 GitHub 更新 ${FILE_NAME}...${NC}"
    if wget -q -O "${TARGET_PATH}.tmp" "${REPO_URL}/${FILE_NAME}" && [ -s "${TARGET_PATH}.tmp" ]; then
        mv "${TARGET_PATH}.tmp" "$TARGET_PATH"
        echo -e "${GREEN}✓ 已更新: ${FILE_NAME}${NC}"
        return 0
    fi

    rm -f "${TARGET_PATH}.tmp"
    echo -e "${RED}${FILE_NAME} 更新失败，请检查 GitHub 网络。${NC}"
    return 1
}

function current_installer_path() {
    local SOURCE_PATH="${BASH_SOURCE[0]}"
    if [[ "$SOURCE_PATH" = /* ]]; then
        printf '%s\n' "$SOURCE_PATH"
    else
        printf '%s/%s\n' "$(pwd)" "$SOURCE_PATH"
    fi
}

function update_installer_file() {
    local INSTALLER_PATH=""
    local INSTALLER_DIR=""
    local TMP_INSTALLER=""

    INSTALLER_PATH="$(current_installer_path)"
    INSTALLER_DIR="$(dirname "$INSTALLER_PATH")"
    TMP_INSTALLER="${INSTALLER_DIR}/.${INSTALLER_NAME}.tmp"

    echo -e "${YELLOW}从 GitHub 更新 ${INSTALLER_NAME}...${NC}"
    if wget -q -O "$TMP_INSTALLER" "$INSTALL_URL" && [ -s "$TMP_INSTALLER" ]; then
        mv "$TMP_INSTALLER" "$INSTALLER_PATH"
        chmod +x "$INSTALLER_PATH" 2>/dev/null || true
        echo -e "${GREEN}✓ 已更新: ${INSTALLER_NAME} -> ${INSTALLER_PATH}${NC}"
        return 0
    fi

    rm -f "$TMP_INSTALLER"
    echo -e "${RED}${INSTALLER_NAME} 更新失败，请检查 GitHub 网络。${NC}"
    return 1
}

echo -e "${BLUE}=============================================================${NC}"
echo -e "${BLUE}    阿里云 CDT 流量监控 & 日报 一键部署/管理脚本 (修复增强版)  ${NC}"
echo -e "${BLUE}=============================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}请使用 root 权限运行 (sudo -i)${NC}"
  exit 1
fi

# ================= 核心功能函数 =================

function setup_bot_service() {
    echo -e "${YELLOW}>> 配置 Telegram 机器人远程管理服务...${NC}"

    if command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
        cat > "/etc/systemd/system/${BOT_SERVICE}" <<EOF
[Unit]
Description=Aliyun Monitor Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${TARGET_DIR}
ExecStart=${VENV_DIR}/bin/python -u ${TARGET_DIR}/bot.py
Restart=always
RestartSec=5
StandardOutput=append:${TARGET_DIR}/bot.log
StandardError=append:${TARGET_DIR}/bot.log

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable "${BOT_SERVICE}" >/dev/null 2>&1
        systemctl restart "${BOT_SERVICE}"
        sleep 1
        if systemctl is-active --quiet "${BOT_SERVICE}"; then
            echo -e "${GREEN}✓ Telegram 机器人服务已启动: ${BOT_SERVICE}${NC}"
        else
            echo -e "${RED}✗ Telegram 机器人服务启动失败，请查看日志：journalctl -u ${BOT_SERVICE} -n 80 --no-pager${NC}"
            return 1
        fi
    else
        crontab -l > /tmp/cron_bk 2>/dev/null
        grep -v "aliyun_monitor_bot" /tmp/cron_bk > /tmp/cron_clean
        echo "@reboot nohup ${VENV_DIR}/bin/python ${TARGET_DIR}/bot.py >> ${TARGET_DIR}/bot.log 2>&1 #aliyun_monitor_bot" >> /tmp/cron_clean
        crontab /tmp/cron_clean
        rm -f /tmp/cron_bk /tmp/cron_clean
        pkill -f "${TARGET_DIR}/bot.py" 2>/dev/null || true
        nohup "${VENV_DIR}/bin/python" "${TARGET_DIR}/bot.py" >> "${TARGET_DIR}/bot.log" 2>&1 &
        echo -e "${GREEN}✓ Telegram 机器人已启动，并写入 @reboot 自启任务${NC}"
    fi
}

function install_or_restart_bot() {
    echo -e "${YELLOW}>> 更新并重启 Telegram 机器人...${NC}"
    install_script_file "ddns.py" || return 1
    install_script_file "bot.py" || return 1
    install_script_file "report.py" || return 1
    setup_bot_service
}

function update_scripts_keep_config() {
    echo -e "${YELLOW}>> 更新脚本文件，保留现有配置...${NC}"
    download_script_file "monitor.py" || return 1
    download_script_file "report.py" || return 1
    download_script_file "bot.py" || return 1
    download_script_file "ddns.py" || return 1
    update_installer_file || return 1
    echo -e "${GREEN}✓ 脚本文件检查完成，未修改 ${CONFIG_FILE}${NC}"
}

# 收集单个用户信息的函数
function get_single_user_json() {
    local AK="" SK="" REGION="" INSTANCE="" NAME="" LIMIT="" BILL_ENDPOINT="" CURRENCY=""
    local SCHEDULE_ENABLED="false" SCHEDULE_START="00:00" SCHEDULE_END="23:59"
    local DDNS_ENABLED="false" DDNS_TOKEN="" DDNS_ZONE_ID="" DDNS_RECORD_NAME="" DDNS_RECORD_TYPE="A"

    echo -e "\n${BLUE}>> 配置阿里云账号/实例信息${NC}"
    read -p "请输入备注名 (例如 HK-Server): " NAME
    
    echo -e "${CYAN}💡 提示: AccessKey 在 RAM 用户详情页 -> 创建 AccessKey${NC}"
    read -p "AccessKey ID: " AK
    read -p "AccessKey Secret: " SK
    
    # --- 按实例区分国内外账单体系 ---
    echo -e "\n${CYAN}💡 提示: 请选择该账号所属的阿里云类型 (决定账单查询节点与货币单位)${NC}"
    echo "  1) 国内区 (阿里云中国站，人民币 ￥ 结算)"
    echo "  2) 国际区 (阿里云国际站，美元 $ 结算)"
    read -p "请选择 (1-2, 默认 1): " ACC_TYPE_OPT
    if [ "$ACC_TYPE_OPT" == "2" ]; then
        BILL_ENDPOINT="business.ap-southeast-1.aliyuncs.com"
        CURRENCY="$"
    else
        BILL_ENDPOINT="business.aliyuncs.com"
        CURRENCY="¥"
    fi
    echo -e "${GREEN}已设置为: 账单节点=$BILL_ENDPOINT | 货币=$CURRENCY${NC}\n"
    # --------------------------------------

    echo -e "${CYAN}💡 提示: 请选择 ECS 实例所在的区域 (输入数字)${NC}"
    echo "  1) 香港 (cn-hongkong)"
    echo "  2) 新加坡 (ap-southeast-1)"
    echo "  3) 日本-东京 (ap-northeast-1)"
    echo "  4) 美国-硅谷 (us-west-1)"
    echo "  5) 美国-弗吉尼亚 (us-east-1)"
    echo "  6) 德国-法兰克福 (eu-central-1)"
    echo "  7) 英国-伦敦 (eu-west-1)"
    echo "  8) 手动输入其他区域代码"
    read -p "请选择 (1-8): " REGION_OPT

    case $REGION_OPT in
        1) REGION="cn-hongkong" ;;
        2) REGION="ap-southeast-1" ;;
        3) REGION="ap-northeast-1" ;;
        4) REGION="us-west-1" ;;
        5) REGION="us-east-1" ;;
        6) REGION="eu-central-1" ;;
        7) REGION="eu-west-1" ;;
        *) read -p "请输入 Region ID (如 cn-shanghai): " REGION ;;
    esac

    echo -e "${CYAN}💡 提示: 请前往 ECS 控制台 -> 实例列表 -> 实例 ID 列 (以 i- 开头)${NC}"
    read -p "ECS 实例 ID: " INSTANCE
    
    read -p "节省停机阈值 (GB, 默认180): " LIMIT
    LIMIT=${LIMIT:-180}

    echo -e "\n${CYAN}💡 可选: 为该实例设置每日运行时段，用于多台服务器按定时计划使用 CDT${NC}"
    echo -e "${CYAN}   示例: A 机器 00:00-12:00，B 机器 12:00-00:00${NC}"
    read -p "是否启用该实例的定时运行窗口? (y/n, 默认 n): " SCHEDULE_OPT
    if [[ "$SCHEDULE_OPT" =~ ^[Yy]$ ]]; then
        SCHEDULE_ENABLED="true"
        prompt_hhmm SCHEDULE_START "开始时间 HH:MM (默认 00:00): " "00:00"
        prompt_hhmm SCHEDULE_END "结束时间 HH:MM (默认 12:00, 可跨天如 20:00-08:00): " "12:00"
    fi

    echo -e "\n${CYAN}💡 可选: 为该实例配置 Cloudflare DDNS，同步时机为实例处于运行窗口内且 Running${NC}"
    read -p "是否启用该实例的 Cloudflare DDNS? (y/n, 默认 n): " DDNS_OPT
    if [[ "$DDNS_OPT" =~ ^[Yy]$ ]]; then
        DDNS_ENABLED="true"
        read -p "Cloudflare API Token: " DDNS_TOKEN
        read -p "Cloudflare Zone ID: " DDNS_ZONE_ID
        read -p "DNS 记录完整域名 (如 hk.example.com): " DDNS_RECORD_NAME
        read -p "DNS 记录类型 (默认 A，第一版仅支持 A): " DDNS_RECORD_TYPE
        DDNS_RECORD_TYPE=${DDNS_RECORD_TYPE:-A}
    fi

    # 将构建好的 JSON 字符串赋值给全局变量 (去除了 resgroup，加入了 bill_endpoint 和 currency)
    CURRENT_USER_JSON="{\"name\": \"$NAME\", \"ak\": \"$AK\", \"sk\": \"$SK\", \"region\": \"$REGION\", \"instance_id\": \"$INSTANCE\", \"traffic_limit\": $LIMIT, \"quota\": 200, \"bill_endpoint\": \"$BILL_ENDPOINT\", \"currency\": \"$CURRENCY\", \"paused\": false, \"schedule_enabled\": $SCHEDULE_ENABLED, \"schedule_start\": \"$SCHEDULE_START\", \"schedule_end\": \"$SCHEDULE_END\", \"ddns_enabled\": $DDNS_ENABLED, \"ddns_provider\": \"cloudflare\", \"ddns_token\": \"$DDNS_TOKEN\", \"ddns_zone_id\": \"$DDNS_ZONE_ID\", \"ddns_record_name\": \"$DDNS_RECORD_NAME\", \"ddns_record_type\": \"$DDNS_RECORD_TYPE\"}"
}

# 完整安装流程 (首次运行)
function run_full_install() {
    # 1. 目录准备
    if [ ! -d "$TARGET_DIR" ]; then
        mkdir -p "$TARGET_DIR"
        echo -e "${GREEN}创建目录: ${TARGET_DIR}${NC}"
    fi

    # 2. 安装依赖
    echo -e "${YELLOW}>> 安装系统依赖...${NC}"
    if [ -f /etc/debian_version ]; then
        apt-get update -y && apt-get install -y python3 python3-venv python3-pip cron wget
    elif [ -f /etc/redhat-release ]; then
        yum install -y python3 python3-pip cronie wget
        systemctl enable crond && systemctl start crond
    fi

    # 3. 虚拟环境
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        echo -e "${GREEN}虚拟环境创建完成。${NC}"
    fi

    echo -e "${YELLOW}>> 安装 Python 依赖库...${NC}"
    "$VENV_DIR/bin/pip" install requests aliyun-python-sdk-core aliyun-python-sdk-ecs aliyun-python-sdk-bssopenapi --upgrade >/dev/null 2>&1

    # 4. 获取源码：首次安装/重新初始化时从 GitHub 拉取最新脚本
    echo -e "${YELLOW}>> 获取监控脚本...${NC}"
    download_script_file "monitor.py" || exit 1
    download_script_file "report.py" || exit 1
    download_script_file "bot.py" || exit 1
    download_script_file "ddns.py" || exit 1

    if [ ! -s "${TARGET_DIR}/monitor.py" ] || [ ! -s "${TARGET_DIR}/report.py" ] || [ ! -s "${TARGET_DIR}/bot.py" ] || [ ! -s "${TARGET_DIR}/ddns.py" ]; then
        echo -e "${RED}脚本获取失败！请检查 GitHub 网络。${NC}"
        exit 1
    fi

    # 6. 交互式配置 Telegram
    echo -e "\n${BLUE}### 配置 Telegram ###${NC}"
    echo -e "1. 联系 ${CYAN}@BotFather${NC} -> 创建机器人获取 Token"
    echo -e "2. 联系 ${CYAN}@userinfobot${NC} -> 获取您的 Chat ID"
    read -p "请输入 Telegram Bot Token: " TG_TOKEN
    read -p "请输入 Telegram Chat ID: " TG_ID

    # 7. 配置阿里云对象
    USERS_JSON=""
    USER_COUNT=0
    while true; do
        get_single_user_json
        USER_COUNT=$((USER_COUNT + 1))
        
        if [ -z "$USERS_JSON" ]; then
            USERS_JSON="$CURRENT_USER_JSON"
        else
            USERS_JSON="$USERS_JSON, $CURRENT_USER_JSON"
        fi

        echo ""
        NEXT_USER_INDEX=$((USER_COUNT + 1))
        read -p "是否继续添加第 ${NEXT_USER_INDEX} 个账号/实例? (y/n): " CONTIN
        if [[ ! "$CONTIN" =~ ^[Yy]$ ]]; then
            break
        fi
    done

    # 8. 生成配置文件
    cat > "$CONFIG_FILE" <<EOF
{
    "telegram": {
        "bot_token": "$TG_TOKEN",
        "chat_id": "$TG_ID"
    },
    "users": [
        $USERS_JSON
    ]
}
EOF
    echo -e "${GREEN}配置文件已生成: ${CONFIG_FILE}${NC}"
    chmod 600 "$CONFIG_FILE"

    # 9. 设置 Crontab
    echo -e "${YELLOW}>> 配置定时任务...${NC}"
    crontab -l > /tmp/cron_bk 2>/dev/null
    grep -v "aliyun_monitor" /tmp/cron_bk > /tmp/cron_clean
    echo "*/5 * * * * ${VENV_DIR}/bin/python ${TARGET_DIR}/monitor.py >> ${TARGET_DIR}/monitor.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean
    echo "0 9 * * * ${VENV_DIR}/bin/python ${TARGET_DIR}/report.py >> ${TARGET_DIR}/report.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean
    crontab /tmp/cron_clean
    rm /tmp/cron_bk /tmp/cron_clean

    setup_bot_service

    echo -e "\n${GREEN}🎉 安装与配置完成！${NC}"
    echo -e "您可以使用以下命令手动测试日报发送："
    echo -e "${YELLOW}${VENV_DIR}/bin/python ${TARGET_DIR}/report.py${NC}"
    echo -e "Telegram 机器人管理入口：发送 ${YELLOW}/menu${NC}"
}

# 管理菜单 (二次运行)
function show_configured_instances() {
    echo -e "\n${BLUE}当前监控的实例列表：${NC}"
    python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        paused = '已暂停' if u.get('paused') or u.get('disabled') else '运行中'
        enabled = bool(u.get('schedule_enabled'))
        start = u.get('schedule_start', '00:00')
        end = u.get('schedule_end', '23:59')
        schedule = f'{start}-{end}' if enabled else '全天运行'
        ddns = u.get('ddns_record_name') if u.get('ddns_enabled') else '未启用'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 区域: {u.get(\"region\")} | 状态: {paused} | 计划: {schedule} | DDNS: {ddns}')
"
}

function run_manage_menu() {
    while true; do
        echo -e "\n${GREEN}=====================================${NC}"
        echo -e "${YELLOW}已检测到存在配置文件，请选择管理操作：${NC}"
        echo "实例管理："
        echo "1) 查看实例状态 (List)"
        echo "2) 添加实例 (Add)"
        echo "3) 修改实例配置/DDNS (Edit)"
        echo "4) 修改运行窗口 (Schedule)"
        echo "5) 暂停/恢复监控 (Pause/Resume)"
        echo "6) 删除实例 (Delete)"
        echo ""
        echo "系统维护："
        echo "7) 更新脚本并重启服务 (Update)"
        echo "8) 重新初始化配置 (Reset Config)"
        echo "9) 卸载并清理脚本 (Uninstall)"
        echo "0) 退出 (Exit)"
        echo -e "${GREEN}=====================================${NC}"
        read -p "请输入序号 (0-9): " MENU_OPT

        case $MENU_OPT in
            1)
                show_configured_instances
                ;;
            2)
                get_single_user_json
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
data['users'].append(json.loads('''$CURRENT_USER_JSON'''))
with open('$CONFIG_FILE', 'w') as f:
    json.dump(data, f, indent=4)
"
                echo -e "${GREEN}✅ 实例添加成功！配置文件已更新。${NC}"
                ;;
            6)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        enabled = bool(u.get('schedule_enabled'))
        start = u.get('schedule_start', '00:00')
        end = u.get('schedule_end', '23:59')
        schedule = f'{start}-{end}' if enabled else '全天运行'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 区域: {u.get(\"region\")} | 计划: {schedule}')
"
                echo ""
                read -p "请输入要删除的实例序号 (输入 q 取消): " DEL_IDX
                if [[ "$DEL_IDX" == "q" || -z "$DEL_IDX" ]]; then
                    continue
                fi
                python3 -c "
import json, sys
idx = int('$DEL_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    removed = data['users'].pop(idx)
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4)
    print(f'\n\033[0;32m✅ 成功删除实例: {removed.get(\"name\")} ({removed.get(\"instance_id\")})\033[0m')
except Exception as e:
    print(f'\n\033[0;31m❌ 删除失败: 无效的序号 {idx}\033[0m')
"
                ;;
            5)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        paused = '已暂停' if u.get('paused') or u.get('disabled') else '运行中'
        enabled = bool(u.get('schedule_enabled'))
        start = u.get('schedule_start', '00:00')
        end = u.get('schedule_end', '23:59')
        schedule = f'{start}-{end}' if enabled else '全天运行'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 状态: {paused} | 计划: {schedule}')
"
                echo ""
                read -p "请输入要切换暂停/恢复的实例序号 (输入 q 取消): " TOGGLE_IDX
                if [[ "$TOGGLE_IDX" == "q" || -z "$TOGGLE_IDX" ]]; then
                    continue
                fi
                python3 -c "
import json
idx = int('$TOGGLE_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    user = data['users'][idx]
    paused = bool(user.get('paused') or user.get('disabled'))
    user['paused'] = not paused
    user.pop('disabled', None)
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4)
    state = '已暂停' if user['paused'] else '已恢复'
    print(f'\n\033[0;32m✅ 成功切换实例: {user.get(\"name\")} ({user.get(\"instance_id\")}) -> {state}\033[0m')
except Exception:
    print(f'\n\033[0;31m❌ 操作失败: 无效的序号 {idx}\033[0m')
"
                ;;
            3)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        paused = '已暂停' if u.get('paused') or u.get('disabled') else '运行中'
        enabled = bool(u.get('schedule_enabled'))
        start = u.get('schedule_start', '00:00')
        end = u.get('schedule_end', '23:59')
        schedule = f'{start}-{end}' if enabled else '全天运行'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 区域: {u.get(\"region\")} | 阈值: {u.get(\"traffic_limit\", 180)}GB | 状态: {paused} | 计划: {schedule}')
"
                echo ""
                read -p "请输入要修改基础配置的实例序号 (输入 q 取消): " EDIT_IDX
                if [[ "$EDIT_IDX" == "q" || -z "$EDIT_IDX" ]]; then
                    continue
                fi

                echo -e "${CYAN}提示: 以下字段直接回车表示保持原值。${NC}"
                read -p "新的备注名: " EDIT_NAME
                read -p "新的 AccessKey ID: " EDIT_AK
                read -p "新的 AccessKey Secret: " EDIT_SK

                echo -e "\n${CYAN}账号类型 (影响账单节点与货币)，直接回车保持原值：${NC}"
                echo "  1) 国内区 (business.aliyuncs.com / ¥)"
                echo "  2) 国际区 (business.ap-southeast-1.aliyuncs.com / $)"
                read -p "请选择 (1-2): " EDIT_ACC_TYPE

                echo -e "\n${CYAN}ECS 区域，直接回车保持原值：${NC}"
                echo "  1) 香港 (cn-hongkong)"
                echo "  2) 新加坡 (ap-southeast-1)"
                echo "  3) 日本-东京 (ap-northeast-1)"
                echo "  4) 美国-硅谷 (us-west-1)"
                echo "  5) 美国-弗吉尼亚 (us-east-1)"
                echo "  6) 德国-法兰克福 (eu-central-1)"
                echo "  7) 英国-伦敦 (eu-west-1)"
                echo "  8) 手动输入其他区域代码"
                read -p "请选择 (1-8): " EDIT_REGION_OPT
                EDIT_REGION=""
                case $EDIT_REGION_OPT in
                    1) EDIT_REGION="cn-hongkong" ;;
                    2) EDIT_REGION="ap-southeast-1" ;;
                    3) EDIT_REGION="ap-northeast-1" ;;
                    4) EDIT_REGION="us-west-1" ;;
                    5) EDIT_REGION="us-east-1" ;;
                    6) EDIT_REGION="eu-central-1" ;;
                    7) EDIT_REGION="eu-west-1" ;;
                    8) read -p "请输入 Region ID (如 cn-shanghai): " EDIT_REGION ;;
                    "") EDIT_REGION="" ;;
                    *) echo -e "${YELLOW}未识别的区域选项，将保持原值。${NC}" ;;
                esac

                read -p "新的 ECS 实例 ID: " EDIT_INSTANCE
                read -p "新的节省停机阈值 GB: " EDIT_LIMIT

                echo -e "\n${CYAN}Cloudflare DDNS，直接回车保持原值，输入 y 开启/更新，输入 n 关闭：${NC}"
                read -p "是否启用 DDNS? (y/n): " EDIT_DDNS_ENABLE
                EDIT_DDNS_TOKEN=""
                EDIT_DDNS_ZONE_ID=""
                EDIT_DDNS_RECORD_NAME=""
                EDIT_DDNS_RECORD_TYPE=""
                if [[ "$EDIT_DDNS_ENABLE" =~ ^[Yy]$ ]]; then
                    read -p "Cloudflare API Token: " EDIT_DDNS_TOKEN
                    read -p "Cloudflare Zone ID: " EDIT_DDNS_ZONE_ID
                    read -p "DNS 记录完整域名 (如 hk.example.com): " EDIT_DDNS_RECORD_NAME
                    read -p "DNS 记录类型 (默认 A，第一版仅支持 A): " EDIT_DDNS_RECORD_TYPE
                    EDIT_DDNS_RECORD_TYPE=${EDIT_DDNS_RECORD_TYPE:-A}
                fi

                EDIT_IDX="$EDIT_IDX" EDIT_NAME="$EDIT_NAME" EDIT_AK="$EDIT_AK" EDIT_SK="$EDIT_SK" EDIT_ACC_TYPE="$EDIT_ACC_TYPE" EDIT_REGION="$EDIT_REGION" EDIT_INSTANCE="$EDIT_INSTANCE" EDIT_LIMIT="$EDIT_LIMIT" EDIT_DDNS_ENABLE="$EDIT_DDNS_ENABLE" EDIT_DDNS_TOKEN="$EDIT_DDNS_TOKEN" EDIT_DDNS_ZONE_ID="$EDIT_DDNS_ZONE_ID" EDIT_DDNS_RECORD_NAME="$EDIT_DDNS_RECORD_NAME" EDIT_DDNS_RECORD_TYPE="$EDIT_DDNS_RECORD_TYPE" CONFIG_FILE="$CONFIG_FILE" python3 -c '
import json
import os
import sys

try:
    idx = int(os.environ["EDIT_IDX"])
except Exception:
    print("\n\033[0;31m❌ 操作失败: 实例序号必须是数字\033[0m")
    sys.exit(0)

with open(os.environ["CONFIG_FILE"], "r") as f:
    data = json.load(f)

try:
    user = data["users"][idx]
except Exception:
    print(f"\n\033[0;31m❌ 操作失败: 无效的序号 {idx}\033[0m")
    sys.exit(0)

fields = {
    "name": os.environ.get("EDIT_NAME", "").strip(),
    "ak": os.environ.get("EDIT_AK", "").strip(),
    "sk": os.environ.get("EDIT_SK", "").strip(),
    "region": os.environ.get("EDIT_REGION", "").strip(),
    "instance_id": os.environ.get("EDIT_INSTANCE", "").strip(),
}
for key, value in fields.items():
    if value:
        user[key] = value

acc_type = os.environ.get("EDIT_ACC_TYPE", "").strip()
if acc_type == "1":
    user["bill_endpoint"] = "business.aliyuncs.com"
    user["currency"] = "¥"
elif acc_type == "2":
    user["bill_endpoint"] = "business.ap-southeast-1.aliyuncs.com"
    user["currency"] = "$"
elif acc_type:
    print("\033[1;33m⚠️ 未识别的账号类型选项，已保持原账单节点与货币。\033[0m")

limit = os.environ.get("EDIT_LIMIT", "").strip()
if limit:
    try:
        limit_value = float(limit)
        user["traffic_limit"] = int(limit_value) if limit_value.is_integer() else limit_value
    except Exception:
        print("\033[1;33m⚠️ 停机阈值不是有效数字，已保持原值。\033[0m")

ddns_enable = os.environ.get("EDIT_DDNS_ENABLE", "").strip().lower()
if ddns_enable in ("y", "yes"):
    user["ddns_enabled"] = True
    user["ddns_provider"] = "cloudflare"
    ddns_fields = {
        "ddns_token": os.environ.get("EDIT_DDNS_TOKEN", "").strip(),
        "ddns_zone_id": os.environ.get("EDIT_DDNS_ZONE_ID", "").strip(),
        "ddns_record_name": os.environ.get("EDIT_DDNS_RECORD_NAME", "").strip(),
        "ddns_record_type": os.environ.get("EDIT_DDNS_RECORD_TYPE", "").strip().upper() or "A",
    }
    for key, value in ddns_fields.items():
        if value:
            user[key] = value
elif ddns_enable in ("n", "no"):
    user["ddns_enabled"] = False

with open(os.environ["CONFIG_FILE"], "w") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)

print("\n\033[0;32m✅ 已更新实例基础配置: {} ({})\033[0m".format(user.get("name"), user.get("instance_id")))
'
                ;;
            4)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        enabled = bool(u.get('schedule_enabled'))
        start = u.get('schedule_start', '00:00')
        end = u.get('schedule_end', '23:59')
        schedule = f'{start}-{end}' if enabled else '全天运行'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 计划: {schedule}')
"
                echo ""
                read -p "请输入要修改定时窗口的实例序号 (输入 q 取消): " SCHEDULE_IDX
                if [[ "$SCHEDULE_IDX" == "q" || -z "$SCHEDULE_IDX" ]]; then
                    continue
                fi
                read -p "是否启用定时运行窗口? (y/n, 默认 y): " SCHEDULE_ENABLE_OPT
                SCHEDULE_ENABLE_OPT=${SCHEDULE_ENABLE_OPT:-y}
                if [[ "$SCHEDULE_ENABLE_OPT" =~ ^[Yy]$ ]]; then
                    prompt_hhmm NEW_SCHEDULE_START "开始时间 HH:MM (默认 00:00): " "00:00"
                    prompt_hhmm NEW_SCHEDULE_END "结束时间 HH:MM (默认 12:00，可跨天): " "12:00"
                    python3 -c "
import json
idx = int('$SCHEDULE_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    user = data['users'][idx]
    user['schedule_enabled'] = True
    user['schedule_start'] = '$NEW_SCHEDULE_START' or '00:00'
    user['schedule_end'] = '$NEW_SCHEDULE_END' or '12:00'
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f'\n\033[0;32m✅ 已更新定时窗口: {user.get(\"name\")} -> {user[\"schedule_start\"]}-{user[\"schedule_end\"]}\033[0m')
except Exception:
    print(f'\n\033[0;31m❌ 操作失败: 无效的序号 {idx}\033[0m')
"
                else
                    python3 -c "
import json
idx = int('$SCHEDULE_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    user = data['users'][idx]
    user['schedule_enabled'] = False
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f'\n\033[0;32m✅ 已关闭定时窗口: {user.get(\"name\")} -> 全天运行\033[0m')
except Exception:
    print(f'\n\033[0;31m❌ 操作失败: 无效的序号 {idx}\033[0m')
"
                fi
                ;;
            7)
                update_scripts_keep_config
                install_or_restart_bot
                echo -e "${YELLOW}提示：${INSTALLER_NAME} 已更新，正在进入新版管理菜单...${NC}"
                exec bash "$(current_installer_path)"
                ;;
            8)
                echo -e "${RED}⚠️ 此操作将重新初始化并覆盖现有的 config.json！${NC}"
                read -p "确认要重新初始化配置吗？(y/n): " CONFIRM_REINSTALL
                if [[ "$CONFIRM_REINSTALL" =~ ^[Yy]$ ]]; then
                    run_full_install
                    exit 0
                fi
                ;;
            9)
                echo -e "${YELLOW}即将调用卸载脚本清理 aliyun_monitor。${NC}"
                TMP_UNINSTALL="/tmp/aliyun_monitor_uninstall.sh"
                if wget -q -O "$TMP_UNINSTALL" "$UNINSTALL_URL" && [ -s "$TMP_UNINSTALL" ]; then
                    bash "$TMP_UNINSTALL"
                    rm -f "$TMP_UNINSTALL"
                    exit 0
                else
                    echo -e "${RED}卸载脚本下载失败，请检查网络后重试。${NC}"
                    rm -f "$TMP_UNINSTALL"
                fi
                ;;
            0)
                echo -e "${GREEN}退出脚本。${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}输入无效，请重新选择。${NC}"
                ;;
        esac
    done
}

# ================= 脚本入口 =================

if [ -f "$CONFIG_FILE" ]; then
    # 如果检测到 config.json 已存在，进入管理菜单
    run_manage_menu
else
    # 首次安装
    run_full_install
fi
