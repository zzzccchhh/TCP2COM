tcp2com 使用速查

完整说明：readme.md
上位机 GitHub：https://github.com/zzzccchhh/TCP2COM
在线工作台：https://zzzccchhh.github.io/TCP2COM/
配套 C5 固件：https://gitee.com/zch_good/esp-c5-wifi

一、安装与启动

1. 在程序目录安装 Python 依赖：
   CLI/Tkinter：py -3 -m pip install -r requirements-tcp2com.txt
   新版桌面：py -3 -m pip install -r requirements-web.txt
   局域网服务：py -3 -m pip install -r requirements-lan.txt
2. 新版前端首次构建：进入 frontend，执行 npm ci、npm run build。
   工作台采用浅色液态玻璃风格，桌面 Chromium/WebView2 支持背景折射；手机和窄屏的大面板使用毛玻璃，支持的 Chromium 小型滑块保留折射。
   效果随前端本地打包，不依赖 CDN 或在线背景图；支持系统“减少动态效果”。授权说明见 third-party-licenses.txt。
   各切换条可点击或拖动玻璃滑块，仅按住鼠标左键或触屏拖动时跟随，普通悬停不移动；松手吸附并仅提交一次选择，触屏纵向滑动可正常滚动。选择字段弹出玻璃面板，COM/波特率仍可手输，选项高亮仅随按下或键盘选择改变。
   滑块经过轨道文字时可见边缘折射，拖动拉伸随速度变化。开关型设置使用胶囊玻璃滑钮，清空原密码保留玻璃勾选框；高级参数平滑展开/收起并保留输入。菜单定位与玻璃准备完成后显示，开合共用380毫秒时长与同一曲线，仅玻璃背景形变，文字原尺寸随面板显示、隐藏，不覆盖输入框。快速反向开关接续当前进度，不重复隐藏文字或延迟入场。
   选项支持方向键、Home/End、Enter、Escape；抽屉、确认框和局域网面板支持平滑打开、关闭。
3. 本机工作台：启动_新版界面.vbs；启动_新版界面.bat 用于构建和诊断。
4. 手机/其他电脑：启动_局域网服务.vbs，使用管理窗口生成的完整链接或二维码。
5. Tkinter：启动_图形界面.bat；原 CLI 使用各启动 BAT 或 tcp2com.py。

在线网页需要服务电脑上的 Python 后端。网页操作的 COM、USB 属于服务电脑；持有效连接链接可自动接入，无需批准。
关闭网页不停止后端业务；关闭管理窗口停止服务并释放设备。首次访问固定在线网址时使用完整连接链接。
服务参数会记住网卡、监听地址/端口和来源，同网卡换 IP 后更新链接。默认网页服务端口 8765，与设备业务端口 8080 不同。

二、选择设备与接口

C5：2.4/5GHz/AUTO；原始 UART=0、UART GPIO 流控=3、四线 SPI 简易写入=4。
旧 C5 SPI WBR=1、UART WBR=2 停用。S3：仅 2.4GHz，保留原始 UART=0、SPI WBR=1、UART WBR=2。
不要把两种固件的接口编号和 MCU 库混用。

C5 SPI 接线：SCK=GPIO23、MOSI=GPIO10、MISO=GPIO9、CS=GPIO5，另需供电和共地。
无需 READY、复位线；GPIO11/12 不再承担握手/复位，UART/UHCI 不初始化。
MCU 库：C5 固件 mcu_library/，本地可导出到 D:\ESP\spi库，只复制 wifi_spi.c/.h。
MCU 实现五个板级端口，调用 wifi_spi_init()、wifi_spi_write(data,length,&written)。
TCP 每批最多 4096 字节，自动多批/尾批；UDP 每次调用一包，最多 512 字节。
SPI 只支持 MCU→C5→网络，电脑发送的业务数据不会送到 MCU；需要下发指令时选 UART。
成功表示 C5 缓存接收，不表示电脑已收到；结果不确定不能直接重发。初始化与批量时序见库 README。

C5 UART GPIO 流控：flow_pin 1/2/3/4 对应 GPIO23/10/9/5，高暂停、低允许，MCU 输入需上拉。
流控线不能与启用的复位线共用，默认 MCU 响应/尾随预算为 5ms 加 4096 字节，实际必须验证。

三、设备配置

1. 选择 ESP32-C5 或 ESP32-S3，连接正确的原生 USB COM。
2. C5 长按 GPIO24 至少3秒，S3 长按 GPIO4，进入配网后读取设备配置。
3. USB 不要求电脑连接 ESP Wi-Fi，不需要启动业务桥接；HTTP 使用配网热点和 http://192.168.4.1/。
4. 编辑接口、Wi-Fi、网络角色、IP/端口和缓存，查看摘要，明确保存并重启。
5. 保存结果未知先重新读取，不能盲目重复保存。

C5 API 为 c5-config-2，保存需当前会话和修订号。旧 SPI 配置恢复时不初始化业务引脚，必须明确保存重启。
旧备份保留网络端口（含8081）及未生效的复位/流控选择，接口要求重新选择；实际选择 UART 后再检查冲突。
固件自身迁移旧 SPI NVS 会清除未生效的复位选择，保留有效网络参数。
导入备份、恢复草稿和启动程序不写 ESP。密码空白表示保留，清空 STA 密码必须明确选择。
启动、型号/地址/端口切换后重新读取设备。无密码草稿和服务参数保存在本地状态文件，不提交仓库。

四、电脑网络与 COM

电脑 TCP 客户端对应 ESP TCP 服务端；电脑 TCP 服务端对应 ESP TCP 客户端。UDP 填双方实际地址和端口。
0.0.0.0 只用于电脑监听，不能作为 ESP 连接目标；电脑实际网卡地址请用管理窗口或 ipconfig 核对。

C5 主动连接电脑示例：
py -3 -B tcp2com.py --device c5 --mode tcp-server --listen 0.0.0.0 --port 8080 --com COM5

电脑连接 C5 示例：
py -3 -B tcp2com.py --device c5 --mode tcp-client --host 192.168.4.1 --port 8080 --com COM5

profiles 示例：
py -3 -B tcp2com.py --config tcp2com_config.json --profile c5-spi-tcp --com COM5
py -3 -B tcp2com.py --config tcp2com_config.json --profile s3-client --com COM5

profiles 只配置电脑，不自动修改 ESP 接口或 NVS。业务 COM 可为真实串口或虚拟 COM 对的一端，不与其他程序共用同一端口。
原生 USB 日志/配置 COM 与业务 COM 不同。电脑 baud 不会改变 MCU SPI 时钟或 ESP UART 波特率。
真实 COM 1,152,000 baud、8N1 的理论载荷约115.2kB/s，可能限制 SPI/Wi-Fi 链路。

五、复位、日志与验证

外部 MCU 复位使用各型号独立 TCP 8081 协议，3秒内点击5次才发送；打开窗口不发请求，结果未知不重试。
C5 仅 UART 模式可启用复位输出，默认关闭；SPI=4 不启用。ESP-01S 的 GPIO0 复位接法和启动约束以固件为准。
控制服务用于可信网络，不代替硬件急停。USB 日志、队列和丢弃计数用于判断瓶颈，网络接收不等于 COM 写入成功。

2026-11-02 软件验证：Python245项，238通过、7按环境跳过；前端构建、26项浏览器回归、C5生产C配置夹具配对通过。
2026-11-03 液态玻璃界面验证：生产构建、Python245项、浏览器29项全部通过；隐藏WebView2玻璃初始化、JS/Python接口、TCP/串口回环及退出清理通过。真实ESP/USB与实体手机效果未在本轮验收。
2026-11-03 液态交互升级验证：生产构建、Python245项、浏览器37项全部通过，覆盖拖动/取消/忙碌状态、键盘选择、触屏横滑与纵向滚动、弹窗进出动画和实例清理；WebView2控件初始化与TCP/串口回环通过。触屏为浏览器模拟，实体手机仍待验证。
2026-11-03 液态控件完善验证：生产构建、浏览器44项、Python245项通过，WebView2初始化、JS/Python接口、TCP/串口回环及退出清理通过。新增轨道文字折射像素对照、玻璃开关鼠标/触屏拖动、取消与忙碌中断、菜单形变、展开/收起和焦点检查，以及减少动态效果/高对比度检查。实体iPhone、Safari折射和真实ESP/USB未验收。
2026-11-03 选项收回与跟随修复验证：生产构建、浏览器46项及WebView2初始化、接口和TCP/串口回环通过，覆盖向上/向下展开、选择/取消关闭时文字像素与尺寸对照，以及悬停不移动、按住拖动和取消恢复。此次未修改Python业务逻辑，实体ESP/USB与iPhone未重新验收。
2026-11-03 菜单开合一致性修复验证：生产构建、浏览器48项及WebView2初始化、接口、TCP/串口回环和退出清理通过。新增贴图加载前保持隐藏、首次可见时布局与贴图稳定、双向曲线一致及快速反向开关的透明度连续检查。实体iPhone/Safari与真实ESP/USB未验收。
真实 ESP/USB、SPI/DMA时序、手机网络和持续无线吞吐仍需板端验证，本轮未烧录或改动车端。
GitHub main 推送触发 Pages 工作流；线上只发布前端，服务电脑仍需更新 Python 和本地前端构建并重启服务。
