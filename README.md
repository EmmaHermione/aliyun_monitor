# 阿里云 CDT 流量监控 & 自动止损脚本 (支持国内/国际双版本)

![OS](https://img.shields.io/badge/OS-Linux-blue?logo=linux)
![Python](https://img.shields.io/badge/Python-3.x-yellow?logo=python)
![Alibaba Cloud](https://img.shields.io/badge/Alibaba%20Cloud-Domestic%20%26%20International-orange?logo=alibabacloud)

一个不仅为自定义 **Alpine** 系统准备的，更全面支持 **阿里云国内版（人民币结算）** 与 **阿里云国际版（美元结算）** 的 **CDT 公网流量监控 + 自动止损工具**。  
在流量或账单即将失控前 **强制关机**，全面适配多节点区域及 Python 3.12 兼容性问题，真正帮你守住钱包 💰。

---

## 📺 视频教程

<div align="center">
  <a href="https://www.bilibili.com/video/BV1b2rfBnEZg/" target="_blank">
    <img width="650" src="https://images.weserv.nl/?url=i2.hdslb.com/bfs/archive/49eb886eab33d88e1cc88c2d3bd624d7eb703d32.jpg" alt="点击观看演示视频" />
  </a>
  <br><br>
  <a href="https://www.bilibili.com/video/BV1b2rfBnEZg/" target="_blank">
    <img src="https://img.shields.io/badge/Bilibili-点击上方封面或此处观看完整视频-FF8EB3?style=for-the-badge&logo=bilibili&logoColor=white" alt="Bilibili Video Tutorial"/>
  </a>
  <br>
  <p><b>📺 超详尽保姆级从零操作演示与避坑防潮指南！</b></p>
</div>

---

## ✨ 核心特性

- 🌍 **双轨支持**：完美支持中国内地账单系统（￥）与国际账单系统（$）。
- 🛡️ **流量熔断**：默认每 5 分钟检测 CDT 使用量，超过阈值立即关机止损。
- ⏱️ **多实例定时计划**：支持给每台 ECS 单独设置每日运行时段，多账号、多服务器可按计划轮流运行。
- 💵 **底层双端兼容**：绕过 API 限制，动态适配业务节点读取当月实时账单余额。
- 🚀 **防黑洞卡死机制**：内置 SNI 与 IPv6 黑洞自动绕过补丁，保障常驻任务在高延迟或 Python 3.12+ 环境下稳定运行。
- 🔄 **自动恢复**：次月流量重置后自动开机恢复业务。
- 📊 **多账号多地域**：同时监控任意组合（不同账号、不同区域、不同内外版实例）。
- 📩 **Telegram 通知**：异常监控告警 + 每日图文并茂的汇总日报。
- 🤖 **Telegram 机器人管理**：支持菜单化查看实例、开机、关机、重启、查询状态、修改定时窗口，并可随时获取当前日报内容。

---

## ⭐ 运行截图

<div align="center">
  <img src="https://github.com/user-attachments/assets/381e346d-604b-47c7-9970-e4e29c87bfb0" width="320" alt="运行截图" />
  <br>
  <p><i>运行效果预览</i></p>
</div>

---

## 🛠️ 前置准备

### 1️⃣ Telegram 通知参数
- 创建机器人并获取 Token：[@BotFather](https://t.me/BotFather)
- 获取您接收消息的 Chat ID：[@userinfobot](https://t.me/userinfobot)

### 2️⃣ 阿里云 RAM 权限设置
为了安全起见，**强烈建议不要使用主账号**。请前往阿里云 RAM 访问控制台创建子用户并授予系统权限：
- 🇨🇳 **国内版 RAM 权限设置入口**：👉 [点击进入阿里云国内站 RAM 控制台](https://ram.console.aliyun.com/users)
- 🌐 **国际版 RAM 权限设置入口**：�� [点击进入阿里云国际站 RAM 控制台](https://ram.console.alibabacloud.com/users)

需要授予的安全权限：
- `AliyunECSFullAccess`（含开关机与查询权限）
- `AliyunCDTReadOnlyAccess` 或 `AliyunCDTFullAccess`（查询流量）
- `AliyunBSSReadOnlyAccess`（查询财务与账单模块）

*(若需要了解详细的创建与使用流程，请查阅本项目内的 [实例开通指南](实例开通.md))*

---

## （一） Alpine Linux（VNC）初始化（可选，针对底层系统玩家）

> ⚠️ **如果您是普通的 Linux (如 Ubuntu/Debian) 用户，请直接跳过本节至 "(三) 一键安装"，本节仅适用于脱水版 Alpine 系统。**

1. 登录阿里云实例的 **VNC 控制台**
2. 复制本项目中 `vnc.sh` 的全量内容。您可以直接一键复制执行以下命令来获取：
前往 GitHub 仓库直接打开 [vnc.sh](https://raw.githubusercontent.com/EmmaHermione/aliyun_monitor/main/vnc.sh) 复制源码全文
3. 将代码 **完整粘贴到 VNC 界面并回车执行**。
4. 初始完毕后即可按以下默认信息 SSH 远程登录：
   - **用户名**：`root`
   - **初始化密码**：`yiwan123`

## （二） Alpine 修复 GRUB 引导并重装 Debian 13 (可选扩展)

> 适用于 **系统无法启动 / GRUB 损坏 / Debian 无法进入** 等进阶场景。通过 **Alpine Linux + chroot** 的方式修复引导并重装 Debian 13。

使用 **root 用户** 登录 Alpine 后，下载并执行脚本：
```bash
wget -qO- https://raw.githubusercontent.com/EmmaHermione/aliyun_monitor/main/install2.sh | sh
```

---

## （三） 一键安装与配置监控 (所有适用者推荐)

使用 **root 用户** 在任意连通互联网的 Linux 服务器或所监控的 ECS 本机上执行：

```bash
wget -O install.sh https://raw.githubusercontent.com/EmmaHermione/aliyun_monitor/refs/heads/master/install.sh
sed -i 's/\r$//' install.sh
bash install.sh
```

脚本将提供丝滑的交互式配置，自动：
* 检测并修齐 Python 运行微环境与 Pip 依赖。
* 拉取已深度解除底层网关 Bug 的执行组件。
* 引导您录入 Telegram 配置、选择站别类型（人民币或美元账单）、输入并配置多个待监控账号。
* 为每个实例单独设置可选的每日运行时段，用于多账号、多服务器按定时计划使用 CDT。
* 设置系统计划任务（Cron），按 **5 分钟/次** 及每天早 9 点执行巡检与汇报。
* 配置 Telegram Bot 远程管理入口，发送 `/menu` 即可打开管理菜单。

> 提示：如果日后需要增加、删除机器、更新脚本、重启 Bot 或调整定时窗口，只需再次运行该脚本命令即可进入管理面板。

---

## ⏱️ 多实例定时计划

本项目支持给每台 ECS 实例单独设置每日运行窗口。该功能适合多账号、多服务器按定时计划使用阿里云 CDT，例如多台服务器分时运行，避免所有实例同时在线。

运行窗口是 **实例级配置**，不限制实例数量。添加 2 台、3 台或更多服务器时，每台都会按照自己配置的 `schedule_start` 和 `schedule_end` 独立执行。

### 工作方式

安装脚本仍然只写入一个全局巡检任务：

```cron
*/5 * * * * /opt/scripts/venv/bin/python /opt/scripts/monitor.py >> /opt/scripts/monitor.log 2>&1 #aliyun_monitor
```

`monitor.py` 每 5 分钟运行一次，并逐个读取 `/opt/scripts/config.json` 中的实例配置：

- 当前时间在该实例的计划时段内：执行原有流量止损逻辑，流量安全则启动或保持运行，流量超标则关机。
- 当前时间不在该实例的计划时段内：如果实例正在运行，则自动关机；如果已经关机，则保持关机。
- 未启用计划时段的实例：保持原来的全天监控行为。

计划时间使用服务器本地时间。请先确认服务器时区，例如中国时间应为：

```bash
timedatectl
timedatectl set-timezone Asia/Shanghai
```

### 安装时配置

添加实例时，脚本会提示：

```text
是否启用该实例的定时运行窗口? (y/n, 默认 n):
开始时间 HH:MM:
结束时间 HH:MM:
```

时间格式必须是 `HH:MM`，例如：

```text
00:00
08:30
13:00
23:59
```

支持跨天窗口，例如 `20:00-08:00` 表示晚上 20:00 开始运行，第二天早上 08:00 结束。

### 多台服务器示例

3 台服务器每天分 3 段运行：

| 实例 | 运行时段 |
| --- | --- |
| Server-A | `00:00-08:00` |
| Server-B | `08:00-16:00` |
| Server-C | `16:00-00:00` |

2 台服务器各运行 12 小时：

| 实例 | 运行时段 |
| --- | --- |
| Server-A | `00:00-12:00` |
| Server-B | `12:00-00:00` |

对应配置字段示例：

```json
{
  "name": "Server-A",
  "schedule_enabled": true,
  "schedule_start": "00:00",
  "schedule_end": "12:00"
}
```

### 后续修改

重新运行安装脚本，检测到已有配置后会进入管理面板：

```text
1) 添加新的监控实例 (Add)
2) 删除已有监控实例 (Delete)
3) 暂停/恢复监控实例 (Pause/Resume)
4) 修改实例定时运行窗口 (Schedule)
5) 更新脚本文件，保留配置 (Update)
6) 启动/重启 Telegram 机器人 (Bot)
7) 重新初始化配置 (Reset Config)
8) 卸载并清理监控脚本 (Uninstall)
9) 退出脚本 (Exit)
```

选择 `4) 修改实例定时运行窗口 (Schedule)`，即可为已有实例开启、关闭或修改运行窗口。

选择 `5) 更新脚本文件，保留配置 (Update)` 会从远程仓库拉取最新的 `monitor.py`、`report.py`、`bot.py`，但不会修改 `/opt/scripts/config.json`。

选择 `6) 启动/重启 Telegram 机器人 (Bot)` 会使用 `/opt/scripts` 下已有的 `bot.py` 和 `report.py` 启动或重启 Bot；如果文件缺失，脚本会尝试从远程仓库下载。

选择 `7) 重新初始化配置 (Reset Config)` 才会重新进入完整配置流程，并覆盖现有 `/opt/scripts/config.json`。这是危险操作，请确认已备份配置后再执行。

也可以直接编辑 `/opt/scripts/config.json`：

```json
"schedule_enabled": true,
"schedule_start": "00:00",
"schedule_end": "12:00"
```

注意：如果多个实例时间段重叠，它们会同时运行；如果时间段之间有空档，空档期间没有实例运行。

---

## 🤖 Telegram Bot 远程管理

安装完成后，在 Telegram 中向您的机器人发送：

```text
/menu
```

即可打开实例管理菜单。主菜单会列出所有 ECS 实例，并提供 `📊 获取报告` 按钮。选中某台实例后，Bot 会直接展示该实例的状态、规格、IP、运行计划、流量、账单与预警信息。

选中某台实例后，可以执行：

- `🟢 开机`
- `🔴 关机`
- `🔁 重启`
- `✏️ 修改定时`
- `🗑 删除定时`
- `⏸️ 暂停/恢复监控`

点击 `✏️ 修改定时` 后，Bot 会等待您输入新的开始时间和结束时间。直接发送：

```text
08:00 20:00
```

即可把所选实例的运行窗口修改为 `08:00-20:00`。如果时间格式输错，可以直接重新输入，格式必须为 `HH:MM HH:MM`。

也可以直接使用命令：

```text
/menu
/list
/report
/status 机器名或序号
/start 机器名或序号
/stop 机器名或序号
/reboot 机器名或序号
/schedule 机器名或序号 HH:MM HH:MM
/unschedule 机器名或序号
```

其中 `/report` 会立即生成并发送当前日报内容，与每天定时发送的日报使用同一套统计逻辑。

---

## ⏸️ 暂停/恢复某台机器的监控

当某台机器处于特殊状态（例如安全锁定、维护或暂不希望自动开机/关机）时，可以临时暂停监控：

1. 重新运行安装脚本进入管理面板。
2. 选择 **“暂停/恢复监控实例 (Pause/Resume)”**。
3. 选择目标机器（如 `HK-02`）即可切换暂停/恢复状态。

暂停后：
- `monitor.py` 将跳过该机器的巡检与自动开关机。
- `report.py` 会在日报里标注“监控已暂停”。

---

## 👋 卸载

```bash
wget -qO- https://raw.githubusercontent.com/EmmaHermione/aliyun_monitor/main/uninstall.sh | sh
```

---

## ⚠️ 免责声明

1. 本项目仅供学习与技术交流使用。
2. 虽然我们尽力适配和兜底了绝大部分的系统、网络、API 阻断与连接层 BUG，但**作者不对因脚本异常、API 变更、依赖挂除或配置错误导致的任何流量流失及费用直接负责。**
3. **强烈建议同时在阿里云费用中心后台设置「预算告警 / 垫底限额」作为最后的防线。**

---

## ⭐ 欢迎 Star 支持

如果这个项目帮您梳理了多节点的部署或者成功避免了一次“破产”，欢迎点个 ⭐！你的支持是我们持续维护的动力 🙏
