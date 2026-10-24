# ESP-01S / ESP32-C5 / ESP32-S3 网络与串口桥接上位机

支持 TCP 客户端、TCP 服务端和 UDP，将网络原始字节与本地 COM 双向桥接，也可不接 COM，直接保存二进制载荷。提供中文图形界面和命令行，显示连接、速率、队列和丢弃统计；C5/S3 支持独立 USB 诊断及用户配置读写。保留 ESP-01S 的按需复位窗口。

```text
业务上位机 ↔ COM 对的另一端 ↔ 本程序 ↔ Wi-Fi ↔ ESP-01S UART / C5、S3 UART/SPI ↔ 主控
                                                └─ C5/S3 原生 USB：日志及配置
```

本程序不解析逐飞 SPI 包头：包头已由设备固件处理。图像帧、波形、指令的长度与校验仍由业务上位机和主控约定。

## 当前配套版本

| 仓库 | 功能提交 | 说明 |
| --- | --- | --- |
| 本上位机 | `e10b605` | S3 设备选择、GUI/CLI、USB/HTTP 配置及保护逻辑 |
| `D:/ESP/ESP-S3-WIFI` | `73cd257` | N16R8 固件与 S3 SPI 主机适配文件 |
| 本上位机基线修复 | `2482aad` | 串口重连隔离、HTTP 会话校验及 USB 统计 |

S3 使用入口为下文“ESP32-S3 N16R8 快速使用”；C5 原有 profiles 和双频能力保留，ESP-01S 继续使用原透传/复位入口。已验证 123 项上位机回归及 C5/S3 生产 C 解析器配对，实机联调仍待完成。

## 安装与选择模式

安装 Python 3 和依赖。在本目录打开 PowerShell：

```powershell
py -3 -m pip install -r requirements-tcp2com.txt
py -3 -B tcp2com.py --list-coms
py -3 -B tcp2com.py --help
```

第三方依赖只有 `pyserial`。中文图形界面和 ESP-01S 复位弹窗需要 Python 自带的 Tcl/Tk；C5/S3 命令行自动禁用复位弹窗，不依赖 Tk。网络服务端和 UDP 监听需要允许 Windows 防火墙中的 Python 入站连接。

双击 `启动_图形界面.bat`，或运行：

```powershell
py -3 -B tcp2com_gui.py
```

GUI 与原 CLI 共用桥接引擎；`启动_C5.bat`、`启动_S3.bat`、`启动_tcp2com.bat` 启动命令行。GUI 不要求管理员权限，也不自动更改电脑网卡、热点或安装虚拟串口。

## 中文图形界面

默认加载 `tcp2com_config.json` 的 `c5-spi-tcp`。先选择设备、电脑网络角色和业务 COM，点击“启动”；设备角色对照下表。

| 页面 | 可操作内容 |
| --- | --- |
| 网络连接 | ESP-01S/C5/S3、TCP 客户端/服务端/UDP、设备地址或来源筛选、电脑绑定地址、端口、UDP 对端端口和分包载荷 |
| COM / 文件 | 刷新/选择或手工输入串口、波特率、数据位/校验/停止位/流控；也可切换到新建二进制文件接收 |
| 高级参数 | 读写/连接超时、重连、Keepalive、缓冲、统计刷新、调试日志、ESP-01S 控制端口和复位入口开关 |
| 设备用户配置 | 名称随 C5/S3 选择变化，支持 USB / HTTP 读取启动配置、编辑 UART/SPI 和联网配置、撤销、备份/导入、明确保存并重启 |
| USB 诊断 | 选择 USB COM、波特率、追加日志；与 USB 配置共用一个连接，可在未启动业务桥接时单独开启 |
| 运行日志 | 筛选、暂停自动滚动、清空、导出；最多保留 3000 行，工作线程事件队列最多 2000 条，超量计数显示在底部 |

顶部电脑配置支持加载、新建、保存和另存为。同一 JSON 中其他 profiles 保留；保存校验通过后原子替换文件。这里只保存电脑参数，不包含设备 Wi-Fi 密码，也不会写入设备。

编辑参数时现有连接继续运行，底部显示实际使用的设备、网络角色、COM 参数、绑定/对端地址和统计。顶部“参数未应用”表示编辑值尚未用于当前连接；点击“应用参数并重启”才释放旧连接并启动新参数。统计刷新、配置连接方式/地址在 GUI 中即时生效，其他业务参数需重启。USB 页的应用只替换 USB 连接；已打开的业务 COM 与 USB COM 必须不同。配置请求进行时暂停替换连接的操作，关闭主窗口仍可取消等待。

最小化窗口继续透传。关闭主窗口会停止桥接、关闭串口和 USB，并等待正在进行的配置请求返回；关闭 ESP-01S 复位子窗口仅隐藏它。C5/S3 模式始终禁用旧 TCP 8081 复位。USB 面板显示已有 `stats` 的原始字段及距更新时间；超过 15 秒标为过期，串口断开显示离线并继续重试，旧数值不能证明设备仍在线。

## ESP32-S3 N16R8 快速使用

对应当前 `D:/ESP/ESP-S3-WIFI` 固件。GUI 原默认仍为 C5 配置；S3 使用时先在电脑配置下拉框选择 `s3-client` 并点击“加载”，或在网络页选择 **ESP32-S3** 并设置所需电脑网络角色。

| 项目 | 当前 S3 | C5 |
| --- | --- | --- |
| 频段 | 仅 2.4 GHz，`band=0`，信道 1～13 | 保留 2.4/5 GHz/AUTO |
| 配网按键 | GPIO4，长按至少 3 秒后松开 | GPIO24 |
| 原生 USB | GPIO19 D− / GPIO20 D+ | GPIO13 D− / GPIO14 D+ |
| 配网热点 | `ESP32-S3-Setup` | `ESP32-C5-Setup` |
| 出厂 AP 密码 | `change-me-s3` | `change-me-c5` |
| 业务 AP / TCP | `ESP32-S3-Bridge`，`192.168.4.1:8080` | 按 C5 保存配置 |

**USB 单线配置**：选择 ESP32-S3，进入“ESP32-S3 用户配置”，选择 USB 和原生 USB 的 COM，点击“连接 / 应用 USB”。长按 GPIO4 松开进入配网，点击“读取 ESP32-S3”；无需启动业务桥接。编辑后点击“保存并重启 ESP32-S3”并确认。USB 日志与配置只占用一个连接，使用前退出其他占用该 COM 的 monitor 或工具。

**HTTP 配置**：进入配网后，电脑连接 `ESP32-S3-Setup`，使用当前 AP 密码，地址为 `http://192.168.4.1/`。USB/HTTP 都只能在配网模式配置。保存结果不明时先重新进入配网读取核对，不自动重发。

USB 根据 hello 的真实型号严格匹配所选 C5/S3，错选型号会拒绝读取和保存。两者目前共用 `C5CFG ` v2 前缀及 `X-C5-Session` HTTP 保存头，名称不表示把 S3 伪装成 C5。现有 HTTP API 没有型号字段，HTTP 按所选设备能力校验，须自行确认目标地址。

S3 配置页仅显示 2.4 GHz，客户端读取、编辑、导入和保存也校验频段；不把 5 GHz 或 AUTO 静默转换。切换型号、地址、配置连接方式或 USB COM 会清除读取依据及密码编辑；切回原选项也需要重新读取，旧异步结果不会填回当前页面。USB 重连会使旧读取失效。

新备份使用 `wifi-bridge-user-config-v1`，记录设备类型且不含密码和会话；仍可导入旧 `c5-user-config-v1`。跨型号导入按当前选择的能力校验：2.4 GHz 配置可复用，S3 拒绝 5 GHz/AUTO，导入只更新编辑区。

S3 profiles：

| 配置名称 | 电脑角色 | 适用情况 |
| --- | --- | --- |
| `s3-client` | TCP 客户端，连接 `192.168.4.1:8080` | 匹配 S3 出厂 UART 模式及 SPI 默认 TCP 监听 |
| `s3-spi-tcp` | TCP 服务端，监听 `0.0.0.0:8080` | 主控已命令 S3 主动连接电脑 |
| `s3-spi-udp` | UDP，固定对端 `192.168.137.200:6060` | 主控已建立对应 UDP Socket，地址按实际网络修改 |
| `s3-capture` | TCP 客户端，仅接收文件 | 保存到新建 `received-s3.bin`，文件必须不存在 |

```powershell
# 编辑 COM1 为自己的业务 COM；也可使用已创建虚拟 COM 对的一端
py -3 -B tcp2com.py --config tcp2com_config.json --profile s3-client --com COM5
# 同时采集原生 USB 日志，USB COM 必须与业务 COM 不同
py -3 -B tcp2com.py --device s3 --host 192.168.4.1 --port 8080 --com COM5 --baud 1152000 --usb-port COM8 --usb-log s3-usb.log
# 仅接收二进制；或双击启动_S3.bat，默认加载 s3-client
py -3 -B tcp2com.py --config tcp2com_config.json --profile s3-capture
```

电脑连接 S3 热点或配置电脑移动热点时使用 **2.4 GHz**。S3 UART0 是 GPIO11 TX / GPIO12 RX，不是开发板上常见 GPIO43/44。S3/C5 均禁用 ESP-01S 的 TCP 8081 复位入口。

SPI 必须在主控集成 S3 固件仓库 `compat/zf_wifi_spi_s3.c`，需要 CS 忙确认、DMA 线长补齐及信用流控；原版/C5 适配文件不能替代。上位机兼容不能代替主控适配。接线及集成说明见 `D:/ESP/ESP-S3-WIFI/compat/README.md`；本次未改固件或车端。

兼容保留 `C5Client(url, timeout=4)`、`C5USBClient(monitor, timeout=4)` 和既有导入路径；新调用可传 `device="s3"`。固件仓库原有 S3 命令行配置入口仍可使用，GUI 现在可直接配置 S3。

## 在 GUI 中读取和修改 C5 配置

推荐 **单 Type-C USB 连接**：

1. 首次通过原生 USB 烧录支持 USB 配置的新 C5 固件；之后用一根支持数据的 Type-C 线连接、供电即可。接口需连到 GPIO13 D−/GPIO14 D+，USB 转 UART0 的 COM 不适用。退出 `idf.py monitor` 或其他占用该 COM 的程序。
2. 打开 GUI，在“网络连接”选择 **ESP32-C5**，到“C5 用户配置”把连接方式设为 **USB**，选择 C5 的 COM，点击“连接 / 应用 USB”。此处与“USB 诊断”的 COM 同步，共用一个串口连接。
3. **长按 GPIO24 按键至少 3 秒后松开**，等待两灯交替闪烁、日志提示 `USB configuration open`。配网初始化成功后点击“读取 C5”。电脑**无需连接 Wi-Fi 或启动业务桥接**；业务 COM、网络端口未配置也可读取。UART/SPI 模式均支持。进入配网会暂停业务透传；普通模式只输出 INFO 及 WARN/ERROR 日志，不响应任何 USB 配置命令。

原 **Wi-Fi HTTP** 方式继续保留：长按 C5 GPIO24 至少 3 秒后松开，电脑连接 `ESP32-C5-Setup`，使用当前 AP 密码（出厂 `change-me-c5`）。连接方式选“Wi-Fi HTTP”，地址默认 `http://192.168.4.1/`，点击“读取 C5”。进入配置热点会中断业务网络；普通业务模式的 HTTP 接口返回 403。

两种方式共用以下编辑及保存流程：

1. 界面显示来源 `defaults`、`nvs` 或旧版迁移来源及读取时间。必须成功读取当前连接后才能保存；切换 USB COM、设备重连、退出再进入配网或改用 HTTP 后需重新读取。
2. 编辑接口与联网、AP、电脑热点 STA、共享 Wi-Fi STA 四个子页。支持 UART/SPI、三种联网模式、2.4/5 GHz/AUTO、C5 UART 波特率、TCP 服务端端口、AP SSID/信道/IP/网关/掩码，以及两组 STA 的 SSID、DHCP/静态 IP/网关/掩码/DNS。切换模式不会丢弃其他配置。
3. 密码不会从设备返回。**留空保留原密码**，填写才替换；STA 要改为开放网络，勾选“开放网络 / 清空原密码”。AP 密码必须为 8～63 个 UTF-8 字节，不支持清空。SSID 最长 32 字节；中文按 UTF-8 字节计数。保存前校验频段/信道、端口及子网/DHCP 条件。
4. 点击“保存并重启 C5”并确认，仅提交变化的可写字段；成功响应表示已写入 NVS，约 1.5 秒后重启。USB 等待自动重连；USB / HTTP 均需再次长按进入配网后读取核对。业务连接按新地址和端口恢复。修改业务 AP IP 不改变配置热点固定地址 `192.168.4.1`。

“撤销编辑”恢复上次读取内容。“备份”导出当前编辑区，**始终不含密码**；“导入备份”只更新编辑区，不直接写设备。C5 备份格式与电脑 profiles 不同，恢复到另一台设备时需重新填写所需密码。密码仅用于本次请求，不写入本地配置、备份或配置操作日志。

USB 使用 `C5CFG ` 前缀、**版本 2**、唯一请求编号的 UTF-8 JSON 行，先识别设备并读取，再校验并保存。设备启动标识 `boot`、配网会话 `session` 及串口连接代次共同防止提交旧编辑；退出再进入配网也会使旧会话失效。请求行最多 2304 字节、patch 最多 2048 字节。固件和上位机需配套升级，旧版 USB 配置协议不兼容。

普通模式下 `hello/read/validate/save` 全部静默忽略；超时提示会同时提醒进入配网、检查 COM、固件及端口占用，不能仅凭超时判断设备模式。HTTP 使用 `GET/POST /api/config`，不跟随重定向，503 表示配网会话失效或等待重启。两种方式均不自动重发保存：保存后超时、断连或未收到有效确认，显示“结果待确认”，必须重新进入配网并读取，因为 NVS 可能已经写入。业务模式 HTTP 的 403 或 USB 超时不能证明保存失败。旧固件无法识别新 USB 协议时提示超时，不尝试写入。

HTTP 读取还必须返回 `http_session`；保存通过 `X-C5-Session` 携带该标识。GUI 将地址、启动和配网会话共同绑定到读取结果，拒绝跨配网重入、重启或换设备提交旧编辑。缺少标识的旧固件需同步更新；导入备份不会导入会话。IPv4 各段不允许前导零，例如使用 `192.168.8.200`，不能使用 `192.168.008.200`。

读取结果是启动配置，不反映 CH32 `wifi_spi_init()` 等命令临时覆盖的运行参数。修改的是 NVS 用户配置，工程中的 `esp32c5_config.h` 仍提供出厂默认值。LED/按键极性、GPIO、PSRAM 队列、USB SDK 设置仍为编译配置，不能在此修改。USB 接口用于可信电脑的本地有线配置。

## 电脑网络角色与 C5/S3 行为对照

| 设备 / 主控行为 | 本程序模式 | 地址及端口含义 |
| --- | --- | --- |
| C5/S3 UART 模式，或 SPI 启动后的默认 TCP 监听 | `tcp-client` | `--host` 是设备 IP；`--port` 是设备监听端口，默认 8080 |
| CH32 调用 `wifi_spi_socket_connect("TCP", ...)` | `tcp-server` | `--listen` 是电脑绑定地址；`--port` 是电脑监听端口 |
| CH32 调用 `wifi_spi_socket_connect("UDP", ...)` | `udp` | `--port` 是电脑端口；`--host` / `--peer-port` 是设备 IP / 本地端口 |

**Wi-Fi 联网方式与 Socket 角色分别配置。** C5/S3 自建 AP、连接电脑热点、共享路由器均可使用；C5 支持双频，当前 S3 仅支持 2.4 GHz。本程序不自动切换电脑热点或网卡。

## C5 示例：逐飞 SPI + TCP

以下使用 C5 profile 和主机适配文件。S3 请加载 `s3-spi-tcp`、使用 S3 专用适配文件，并将热点设置为 2.4 GHz；电脑/设备的 Socket 角色一致。

1. 在 C5 配网页面把“主控接口”设为“逐飞 Wi-Fi SPI”，保存并重启。
2. 电脑开启热点；编辑 `tcp2com_config.json` 的 `c5-spi-tcp` 配置，将 `COM1` 改成实际桥接端口。
3. 双击 `启动_C5.bat`。默认选择 `c5-spi-tcp`，先启动电脑监听，再启动 CH32 连接。
4. CH32 调用原逐飞接口，检查返回值：

```c
if (wifi_spi_init("电脑热点名称", "热点密码") == 0)
{
    uint8 failed = wifi_spi_socket_connect("TCP", "192.168.137.1", "8080", "6060");
    /* failed == 0 后再调用 wifi_spi_send_buffer / wifi_spi_read_buffer。 */
}
```

`192.168.137.1` 只是电脑热点网卡 IP 的示例，请用 `ipconfig` 核对。`8080` 是电脑端口，`6060` 是 C5 本地端口。`0.0.0.0` 用于电脑绑定全部 IPv4 网卡，不能填作 CH32 的连接目标。

同样可直接运行：

```powershell
py -3 -B tcp2com.py --device c5 --mode tcp-server --listen 0.0.0.0 --port 8080 --com COM5 --baud 1152000
```

服务端同一时间只接一个连接，立即关闭额外连接。可用 `--host C5实际IP` 筛选来源 IP。断线后电脑继续监听；**CH32 必须再次调用 Socket 连接接口**，上位机不能代替 C5 发起重连。`--reconnect 0` 只控制 TCP 客户端，服务端始终继续监听。

`wifi_spi_init()` 会让 C5 主动连接传入的热点，临时进入 STA；`wifi_spi_socket_connect()` 会关闭默认 TCP 监听。SPI 命令不写 NVS，网页显示的是保存的启动配置。C5 软件 RST 恢复已保存联网配置及默认 TCP 服务端，原客户端模式需要主控重新建立。

## 电脑主动连接 C5

C5 UART 模式直接提供 TCP 服务端。电脑连接 C5 AP 时默认 IP 为 `192.168.4.1`；C5 连接电脑热点或路由器时，使用其实际 IP，可从 USB 日志或终端列表获取。

```powershell
py -3 -B tcp2com.py --device c5 --mode tcp-client --host 192.168.4.1 --port 8080 --com COM5 --baud 1152000
```

SPI 启动后也默认提供同样的 TCP 服务端。但 CH32 应仅完成本地 SPI/FIFO 初始化，再用收发接口；不能照搬会切换网络的原 `wifi_spi_init()` 加 Socket 连接示例。若 CH32 使用原初始化流程，优先选择上一节的电脑 TCP 服务端。

ESP-01S 保持原调用兼容：

```powershell
py -3 -B tcp2com.py --host 192.168.4.1 --port 8080 --com COM5 --baud 1152000
```

不指定 `--device`、`--mode` 时仍默认 `esp01s`、`tcp-client`。原 `启动_tcp2com.bat` 保留当前 `192.168.4.1` 地址以及原有串口、重连和控制端口配置。

## C5 示例：SPI UDP 双向透传

S3 可加载 `s3-spi-udp`，按实际设备 IP 和 Socket 端口修改参数，且主控必须使用 S3 适配文件。

例如 CH32 调用：

```c
wifi_spi_socket_connect("UDP", "192.168.137.1", "8080", "6060");
```

电脑对应运行，`192.168.137.200` 必须替换为 C5 当前 IP：

```powershell
py -3 -B tcp2com.py --device c5 --mode udp --listen 0.0.0.0 --port 8080 --host 192.168.137.200 --peer-port 6060 --com COM5 --udp-payload 1200
```

- 只接收指定 C5 IP **和来源端口** 的报文，其他来源计入拒收；地址变化后应重启并更新参数。
- 接收缓冲固定足够容纳 IPv4 最大 UDP 载荷 65,507 字节，不受 `--bufsize` 缩小影响。空报文只统计，不当作断线。
- COM→UDP 按 `--udp-payload` 分包，默认 1200 字节；COM 本身没有报文边界。C5→电脑的大包仍可能发生 IP 分片。
- UDP 不保证送达、去重和顺序，没有 TCP 背压。显示“已绑定”和最近收包时间，不把绑定成功当作设备在线。网络错误时明确报错退出，修复后重新启动。
- C5 的 UDP 输入通过 SPI 交给 CH32 时仍是字节流。C5 上行约 2 ms 空闲后发包，也支持 `wifi_spi_udp_send_now()`；应用需要帧同步、序号及校验时应自行添加。

## JSON 配置与启动入口

`tcp2com_config.json` 只保存上位机参数，不存 Wi-Fi 密码，不自动写设备配置。程序仅在显式指定 `--config` 时读取，命令行同名参数优先。

```powershell
py -3 -B tcp2com.py --config tcp2com_config.json --profile c5-spi-tcp --com COM5
```

| Profile | 用途 |
| --- | --- |
| `c5-spi-tcp` | C5 主动连接电脑，电脑监听 TCP 8080 |
| `c5-client` | 电脑连接 C5 默认 TCP 服务端 |
| `c5-spi-udp` | UDP 双向桥接，需修改 C5 实际 IP |
| `c5-capture` | 电脑监听 TCP，直接保存为新的 `received.bin` |
| `s3-client` | 电脑连接 S3 默认 TCP 服务端，`启动_S3.bat` 的默认选择 |
| `s3-spi-tcp` | S3 主动连接电脑，电脑监听 TCP 8080 |
| `s3-spi-udp` | S3 UDP 双向桥接，需修改实际设备 IP |
| `s3-capture` | 电脑连接 S3，仅接收至新的 `received-s3.bin` |
| `esp01s` | 原 TCP 客户端与独立复位窗口 |

修改 `启动_C5.bat` 或 `启动_S3.bat` 的 `T2C_PROFILE` 即可选择模式，也可从终端传入额外参数。两个脚本的默认角色不同：C5 脚本默认电脑监听，S3 脚本默认电脑主动连接。BAT 内容保持 ASCII，中文提示由 UTF-8 Python 输出。

超时、重连及速率刷新参数必须为有限数值，且不超过 86400 秒。Profile 键使用参数的下划线形式，如 `peer_port`、`usb_port`。未知键、重复键、错误类型、非法选项和非有限数值会被拒绝。配置中的相对 `capture`、`usb_log` 路径以 JSON 所在目录为基准；命令行相对路径以当前目录为基准。

## COM、虚拟串口与文件接收

本程序只打开已有 COM，不创建或安装虚拟串口。若业务上位机只支持 COM，先用虚拟串口工具创建一对端口：本程序打开一端，业务上位机打开另一端，同一个端口不能同时被两者占用。

真实串口默认 1152000、8N1，理论载荷约 115.2 kB/s，可能限制 SPI/Wi-Fi 的吞吐。虚拟串口是否受波特率影响取决于驱动。这里的波特率只控制电脑本地串口，不会修改设备 UART 波特率或 CH32 SPI 时钟。

无 COM 时可选择仅接收原始载荷：

```powershell
py -3 -B tcp2com.py --device c5 --mode tcp-server --port 8080 --capture received.bin
```

`--capture` 与 `--com` 必须二选一；文件接收模式不向设备发送业务数据。文件使用独占新建，已有文件会报错而不会覆盖；多次 TCP 会话按收到顺序写入同一文件，不插入会话标记或日志。TCP 对端正常关闭前收到的尾部也会写入。文件写入失败会报错退出，统计未写入部分。需要逐帧恢复、缺包检测或文件完成确认时，使用业务协议处理。

## C5/S3 配网页面与 USB 诊断

先长按配网按键至少 3 秒后松开。C5 为 GPIO24 / `ESP32-C5-Setup` / 出厂密码 `change-me-c5`；S3 为 GPIO4 / `ESP32-S3-Setup` / 出厂密码 `change-me-s3`。已修改密码时使用当前 AP 密码。按型号打开网页：

```powershell
py -3 -B tcp2com.py --device c5 --open-config
py -3 -B tcp2com.py --device s3 --open-config
```

此命令只打开浏览器后退出，不启动数据桥接。普通业务模式访问会返回 403。网页可选择 UART/SPI、三种联网模式、频段、AP/STA IP、波特率和端口；明确“保存并重启”才写 NVS。进入配网、保存重启都会中断业务网络，应按修改后的地址/角色重新连接。LED/按键有效电平、GPIO、PSRAM 队列容量仍属于固件编译配置，上位机不能远程修改。

可在数据桥接同时读取独立的 C5/S3 **原生 USB Serial/JTAG** 日志。以下为 C5 示例，S3 示例见快速使用章节：

```powershell
py -3 -B tcp2com.py --config tcp2com_config.json --profile c5-spi-tcp --com COM5 --usb-port COM8 --usb-log c5-usb.log
```

USB 日志端口不能与业务 COM 相同。终端以 `USB |` 前缀显示日志，文本另存为追加日志；不向 TCP、UDP 或二进制载荷文件插入内容。解析并缓存已有 `stats` 字段，包括 IP、信道、RSSI、PSRAM 队列、SPI 错误、丢弃及栈余量；未知日志原样保留。

USB 不可用/断开时通道每 2 秒重试，网络桥接继续。只看日志时不发送 USB 数据；GUI 明确读取/保存时才发送配置命令，命令和回复不写入日志文件。程序不主动脉冲 DTR/RTS；打开串口时线路的实际电气行为仍需实板确认。日志与配置共用本程序内同一个 COM 连接；与其他监视/下载工具不能同时占用同一端口。

## 统计、稳定性与故障排查

- 底部约每 0.25 秒刷新，速率为约 1 秒滚动平均。分别显示网络接收、本地写入、本地→网络发送、两方向队列和累计本地丢弃；UDP 另外显示收发包及来源拒收数。
- 每个方向最多 128 块，每块 16 KiB，约 2 MiB，不含正在处理的批次。TCP 队列满时暂停读取，保持顺序；真实串口或 UDP 上游仍可能继续发送并溢出。
- TCP 客户端默认每 2 秒重连；TCP 服务端断线继续监听。重连前后清理队列，并检查连接代次；发送线程在等待队列后重新读取当前连接，避免误丢新会话数据。
- 串口写超时不可靠地报告已经发出的部分，未确认部分记为丢弃，不盲目重放。断线时队列清理也计入丢弃。UDP 发送部分成功时分别记录已发字节和剩余丢弃量。
- 本地丢弃为零不能证明 SPI、Wi-Fi 或主控 FIFO 没有丢包；应结合设备 USB 诊断及应用序号/校验。TCP/COM 不保留图像帧或 read/recv 块边界。
- 确认 TCP 连接方向、Windows 防火墙、端口占用、设备当前 IP。电脑监听 `0.0.0.0` 不代表 CH32 也应连接这个地址。
- S3 USB 配置超时时，核对是否选择 ESP32-S3、是否使用原生 USB 接口，以及是否长按 GPIO4 进入配网。旧 C5-only 上位机需要更新，业务串口和 USB 配置串口不能混用。
- Keepalive 不是即时断线检测，Windows 探测次数可能由系统控制。真实吞吐、双频表现及长时稳定性需在目标电脑和板上验证。
- `Ctrl+C` 或关闭控制台退出桥接。关闭 ESP-01S 复位窗口只隐藏窗口，透传继续；USB 诊断随主程序退出。

## ESP-01S 独立复位窗口

仅 `--device esp01s --mode tcp-client` 启用；可用 `--no-reset-ui` 完全关闭。命令行入口左键点击或按 `R` 打开独立窗口，打开本身不发送请求。窗口中从首次点击起 3 秒内点 5 次才发送一次请求；关闭窗口只隐藏，不停止透传或重连。

控制连接使用独立 TCP 8081，先验证 `ESP01S-RESET/1 READY`，随后发送带唯一编号的 `RESET`。只收到匹配编号的 `DONE` 才显示完成。发送后掉线/超时为“结果未知”，不会自动重发；重复编号不重复执行。详细协议见 `README_tcp2com.txt`。

ESP-01S GPIO0 开漏连接外部主控低有效复位脚、外部上拉 3.3 V，执行时拉低约 1 秒；它也是启动配置脚，启动波形须实测。该功能是复位，不等同硬件急停。控制服务无身份认证，仅用于可信局域网。

**C5/S3 当前没有 TCP 8081 远程复位服务，对应模式强制关闭旧复位入口。** SPI GPIO12 是 CH32→设备的软件 RST 输入，不能用于电脑远程复位外部主控。上位机也不能远程控制设备 LED、按键或触发配网。

## 目录与验证

| 路径 | 用途 |
| --- | --- |
| `tcp2com.py` | 桥接、字节队列、统计、命令行入口 |
| `tcp2com_gui.py`、`gui_support.py` | 中文 Tkinter/ttk 界面、本地配置读写、后台生命周期及有界日志事件 |
| `device_capabilities.py` | ESP-01S/C5/S3 的设备标识、频段、配网及 USB/复位能力 |
| `c5_config.py` | C5/S3 配置 API、按型号校验、差量提交及密码保护，保留原模块名 |
| `c5_usb_config.py` | USB 设备识别、配置读取、校验/保存及启动标识检查 |
| `network_transport.py` | TCP 单会话监听、固定对端 UDP |
| `bridge_settings.py`、`tcp2com_config.json` | 配置校验与示例 profiles |
| `usb_diagnostics.py` | USB 单连接日志/命令收发、请求匹配、断线及配置结果处理 |
| `console_control.py`、`reset_control.py` | 原控制台入口与 ESP-01S 复位窗口 |
| `启动_C5.bat`、`启动_S3.bat`、`启动_tcp2com.bat` | C5 / S3 / 原 ESP-01S 命令行启动入口 |
| `启动_图形界面.bat` | 检查依赖后通过 pythonw 启动 GUI |
| `tests/` | 网络、配置、诊断及原功能回归测试 |
| `tools/test-firmware-config.py` | 上位机 USB 客户端与 C5/S3 生产 C 解析器夹具配对 |

```powershell
py -3 -B -X utf8 -m unittest discover -s tests -v
```

测试使用本机 TCP/UDP socket、`loop://` 串口和故障注入，覆盖二进制双向传输、第二连接拒绝、重连、部分写入、背压/旧会话清理、65,507 字节 UDP、来源过滤、短连接文件尾部、配置优先级及 USB 失效隔离。原 ESP-01S 协议、窗口和 Windows 原生控制台测试同时运行。Tk 不可用时部分原窗口测试跳过。

2026-10-22 GUI 验证：73 项测试全部通过，无跳过，包含原 40 项回归。新增本机 HTTP 模拟 C5 读写、403/503、超时/断连不重放、非法响应、配置校验和密码保留、无密码备份、profiles 保存、USB 隔离/过期状态、真实 Tk 模式切换/最小化/重启/关闭及高 DPI 布局。已人工检查网络、C5 配置和 USB 页截图。后台线程不持有 Tk 对象；销毁前等待生命周期和配置工作线程结束，Tcl 变量在 UI 线程释放。

单 Type-C 配置验证：93 项上位机测试通过，无跳过。新增覆盖普通模式静默拒绝、配网退出/重入会话失效、校验后退出不提交、GUI 配网提示及旧读取失效、USB 日志/配置共用句柄、分包/旧编号、密码保护、旧固件、启动标识变化、部分写入/保存回复丢失或无效时不重发、关闭取消请求、真实 Tk 单线配置、连接切换、profiles 与保存结果处理；人工检查 USB/HTTP 配置页截图。另用固件仓库 `tools/test-usb-config-pair.py` 将真实 Python USB 工作线程/客户端与生产 C 协议及配置服务配对，读取、拒绝非法参数、保存及重启等待状态通过；串口、NVS 和定时器仍为适配器，不代替实板验证。

尚未完成真实 C5/CH32 联调：需实测 UART/SPI、三种联网方式、2.4/5 GHz、硬件/虚拟 COM、USB 热插拔、应用数据校验、持续吞吐和至少 1 小时负载测试。软件回环通过不代表这些实板检查已完成。

2026-10-22 审查修复回归：98 项测试通过，无跳过。新增验证阻塞串口读取跨重连时丢弃旧数据、新会话接收就绪后才转发、HTTP 旧启动/配网标识拒绝、前导零地址拒绝及多行 USB 统计不串字段。活动 `session` 与 `drops.session`、两个方向的 `queue.*.peak` 和 `rate.*` 分别显示；未知记录继续保留原始日志，不覆盖既有统计。

SPI 连续双向无损流控需要在 CH32 使用 C5 仓库的 `compat/zf_wifi_spi_c5.c` 适配层，并更新 C5 固件，业务仍调用原 `wifi_spi_*` 接口。它按 FIFO 空间协商接收额度，发送队列满时仍允许接收/控制；业务须保留 `send_buffer` 返回的未发送尾部并稍后重试。仅更新电脑端无法修复原 ZF 驱动 FIFO 溢出。接入步骤见 `D:/ESP/ESP-C5-WFI/compat/README.md`。

## 2026-10-24 S3 兼容验证

此前的 10 文件修复已提交为 `2482aad`（修复重连数据隔离、配置会话校验与 USB 统计），提交前 98 项测试通过。S3 上位机兼容已独立提交为 `e10b605`，配套固件为 `73cd257`。

配套功能版本已完成 **123 项回归，通过、无跳过**，包括真实 Tk 界面、C5/S3 能力差异、USB 型号不匹配、频段限制、配置导入、跨设备切换、旧异步结果及 USB 断线后的读取失效，以及 S3 TCP 二进制回环和重连。本次仅更新 README，未重新运行这些测试。

```powershell
py -3 -B -X utf8 -m unittest discover -s tests -q
# 使用现有固件仓库构建出的生产 C 解析器夹具，不改固件源码
py -3 -B tools/test-firmware-config.py --device s3 --fixture D:/ESP/ESP-S3-WIFI/build/host/usb_config_fixture.exe
py -3 -B tools/test-firmware-config.py --device c5 --fixture D:/ESP/ESP-C5-WFI/build/host/usb_config_fixture.exe
```

两款生产 C 解析器与本次客户端/USB 工作线程配对通过，另验证 S3 固件仓库原有命令行配置工具仍兼容。夹具运行模拟串口、NVS 和定时器，不等于真实 USB 或 SPI 验证。没有修改固件/车端，没有烧录，实际 USB 热插拔、Wi-Fi 吞吐及长时间板端联调仍未执行。
