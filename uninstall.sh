#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TARGET_DIR="/opt/scripts"
BOT_SERVICE="aliyun-monitor-bot.service"

say() {
    printf '%b\n' "$1"
}

ask() {
    printf '%b' "$1"
    read -r "$2"
}

is_yes() {
    case "$1" in
        [Yy]|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

clear 2>/dev/null || true
say "${BLUE}=============================================================${NC}"
say "${BLUE}       阿里云 CDT 监控 - 彻底卸载与清理工具 (Safe Mode)      ${NC}"
say "${BLUE}=============================================================${NC}"

if [ "$(id -u)" -ne 0 ]; then
    say "${RED}错误：请使用 root 权限运行 (sudo -i)${NC}"
    exit 1
fi

say "${RED}警告：此操作将执行以下清理：${NC}"
say "  1. 停止并移除相关的 Crontab 定时任务"
say "  2. 永久删除目录: ${TARGET_DIR} (包含配置、日志、虚拟环境)"
say "  3. 清理安装时产生的临时文件"
say ""

ask "确认要执行卸载吗？(y/n): " CONFIRM
if ! is_yes "$CONFIRM"; then
    say "${YELLOW}已取消操作。${NC}"
    exit 0
fi

say ""
say "${YELLOW}>> 正在停止 Telegram 机器人服务...${NC}"
if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$BOT_SERVICE" >/dev/null 2>&1 || true
    systemctl disable "$BOT_SERVICE" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${BOT_SERVICE}"
    systemctl daemon-reload >/dev/null 2>&1 || true
fi

say "${YELLOW}>> [1/3] 正在清理定时任务...${NC}"
if crontab -l > /tmp/cron_backup 2>/dev/null; then
    if grep -q "aliyun_monitor" /tmp/cron_backup; then
        grep -v "aliyun_monitor" /tmp/cron_backup > /tmp/cron_clean
        crontab /tmp/cron_clean
        rm -f /tmp/cron_clean
        say "${GREEN}✓ 已移除监控相关的 Crontab 任务。${NC}"
    else
        say "${GREEN}✓ 未发现相关的 Crontab 任务，跳过。${NC}"
    fi
    rm -f /tmp/cron_backup
else
    say "${GREEN}✓ 当前用户无 Crontab 任务，跳过。${NC}"
fi

say "${YELLOW}>> [2/3] 正在删除程序文件与数据...${NC}"
if [ -d "$TARGET_DIR" ]; then
    rm -rf "$TARGET_DIR"
    if [ ! -d "$TARGET_DIR" ]; then
        say "${GREEN}✓ 已彻底移除目录: ${TARGET_DIR}${NC}"
    else
        say "${RED}✗ 目录删除失败，请手动检查: ${TARGET_DIR}${NC}"
    fi
else
    say "${GREEN}✓ 目录不存在，跳过。${NC}"
fi

say "${YELLOW}>> [3/3] 正在清理下载痕迹...${NC}"
if [ -f "./aliyun-monitor.sh" ]; then
    rm -f "./aliyun-monitor.sh"
    say "${GREEN}✓ 已删除当前目录下的 aliyun-monitor.sh${NC}"
fi
if [ -f "./install.sh" ]; then
    rm -f "./install.sh"
    say "${GREEN}✓ 已删除当前目录下遗留的 install.sh${NC}"
fi

say ""
say "${GREEN}卸载完成！系统已恢复干净。${NC}"

say ""
ask "是否删除此卸载脚本 (uninstall.sh) 以完全清除痕迹？(y/n): " SELF_DEL
if is_yes "$SELF_DEL"; then
    rm -- "$0"
    say "${GREEN}✓ 卸载脚本已自删除。Bye!${NC}"
fi
