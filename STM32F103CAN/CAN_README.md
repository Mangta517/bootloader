# F103 更新应用

当前为已存 BIN 的 CAN 联调入口：`Core/Src/main.c` 直接使用 Flash `0x08008000` 中已验证的 4480 字节 BIN，设置 `APP_FLASH_DONE` 后调用 `app_update_init()`。复位后不再等待串口接收，也不擦除 BIN 数据区。

长度由 `appliaction/app_update.h` 中的 `APP_UPDATE_STORED_SIZE_BYTES` 定义；更换 BIN 后必须同步修改。此入口用于当前已加载的文件，不自动探测文件长度。

启动串口提示 `CAN init OK, 200000 bit/s` 和 `Waiting update command: hyf`。F407 串口发送 `cmd` 后，F407 发出 ID 0 的三字节 `hyf`；F103 收到命令后，以 ID 1 分帧发送已有 BIN。成功进入 `APP_UPDATE_DONE`，失败进入 `APP_UPDATE_ERROR`；重新测试时复位 F103，并让 F407 回到空闲状态后再发送 `cmd`。

原串口接收功能仍保留：需要重新装载 BIN 时，将启动入口恢复为 `App_bootloader_init()`、循环调用 `App_bootloader_update()` 直到 `APP_FLASH_DONE`，最后再调用 `app_update_init()`。`App_bootloader_init()` 的累计接收字节数已恢复为 0。

## CAN 接口

自定义功能函数位于 `interface/int_can.c` 和 `.h`，应用层包含 `int_can.h`：

- `can_init()`：配置过滤器并启动 CAN，调用前完成 `MX_CAN_Init()`。
- `can_send(id, data, len)`：发送标准数据帧，等待硬件完成并检查 TXOK，返回 0 表示总线发送成功、1 表示失败。邮箱满时立即失败，已提交帧最多等待 100 ms，超时请求撤销；当前自动重传关闭，失败不继续发送后续固件。
- `can_receive(header, data)`：轮询 FIFO0，返回 0 表示收到一帧、1 表示无报文或读取失败。

`can_send()` 的缓冲区必须有 8 字节，`len` 为 0～8；HAL 装载邮箱时读取整个 8 字节缓冲区，实际上总线只发送 DLC 指定的字节数。CubeMX 的外设初始化仍位于 `Core`。

## 命令

F407 发送标准数据帧：**ID `0`、DLC `3`、数据 `68 79 66`（ASCII `hyf`）**。大小写、长度和内容必须完全匹配，不带回车、换行或零填充。

F103 的硬件过滤器只接收标准 ID `0` 的数据帧。发送格式如下：

- 数据帧：标准 ID `1`，普通帧 DLC 为 8，尾帧 DLC 为剩余 1～8 字节。
- 数据内容：Flash 中原始 BIN，无包头、序号、长度帧或结束帧，也不发送奇数字节写 Flash 时补齐的 FF。
- 发送循环：剩余字节数从 `app_size_bytes` 开始，够 8 字节就发送 8 字节，不足 8 字节则发送尾包；只有发送成功才扣减剩余长度并推进 Flash 偏移。一次 `app_update_work()` 在发送状态下循环完成整份 BIN。
- 逐帧等待：每帧 CAN 发送成功后等待 10 ms，包括最后一帧，由 `app_update.h` 的 `APP_UPDATE_FRAME_DELAY_MS` 定义。当前接收长度统计测试继续保留此发送节拍。
- 总长度：当前启动设置的 `app_size_bytes`，范围 1～32768 字节。接收端需事先获知有效长度并按接收顺序拼接。

例如 9 字节 BIN 对应 DLC=8 和 DLC=1 两帧。发送完成打印 `CAN send done,len:9`；若第二帧失败，则打印 `CAN send error, offset:8` 并停止。DONE/ERROR 状态不再响应周期 `hyf`；当前联调入口开始新一轮只需复位 F103，无需重新加载 BIN；长度由 APP_UPDATE_STORED_SIZE_BYTES 指定。

当前 Flash 中已有 4480 字节 BIN，复位后直接等待 CAN 命令，无需再发送串口文件。CAN 共发送 560 帧，DLC 均为 8；每帧后等待 10 ms，仅等待时间合计 5.6 秒，另有总线和日志开销。完成打印 `CAN send done,len:4480`。

F407 收到串口 `cmd` 后发送一次 `hyf`，随后累计标准 ID `1` 数据帧的有效字节数，连续 1 秒无有效数据后打印一次 `CAN receive done,len:实际接收字节数` 并回到空闲状态。接收期间不再逐帧打印，尚未写入 W25Q16；总线 TXOK 表示 CAN 帧被确认，不代表 F407 应用已经拼接、校验、写入或执行了固件。

主循环只调用 `app_update_work()`，状态机放在应用层；原来每秒发送测试帧的流程已移除。F407 每次从空闲状态接受 `cmd` 后发送一次 ID `0`、DLC `3` 的 `hyf` 命令。

## 引脚和打印

- CAN：200 kbit/s，F103 PA11/RX、PA12/TX；F407 当前工程使用 PB8/RX、PB9/TX。各自经 CAN 收发器接入 CANH/CANL，共地，总线两端各一个 120 Ω 终端。
- F103 串口：USART1 **PA9/TX** 接 3.3 V USB 转 TTL 的 RXD，**PA10/RX** 接 TXD，GND 共地，**115200、8N1、无流控**。更新命令走 CAN，串口支持打印和循环 DMA 接收，接收接口见 `UART_RECEIVE.md`。

应用层统一使用 `printf()`；`Core/Src/usart.c` 中的 `fputc()` 将字符输出到 USART1，Keil 工程启用 MicroLIB。

启动提示：

```text
CAN init OK, 200000 bit/s
Waiting update command: hyf
```

校验通过：

```text
hyf OK, enter send update
```

发送完成：

```text
CAN send done,len:实际BIN字节数
```

## 验证

主机回归：在当前工程目录运行 `python tools/test_app_update.py`，编译真实应用层与 CAN 驱动，检查启动顺序、严格命令校验、Flash 状态/长度、逐字节分帧、尾帧、尾帧及跨 256/512 字节边界的逐帧等待、32 KiB 边界、三邮箱、tick 回绕、完成状态以及邮箱满/提交失败/TXOK 失败/超时停止。

串口接收及写 Flash 联合测试：`python tools/test_app_flash.py`。

Keil 工程为 `MDK-ARM/STM32F103CAN.uvprojx`。2026-10-31 修复后两套 F103 回归通过，Keil 编译 0 错误、0 警告。已通过 ST-Link 写入并读回核对程序，BIN 数据区全部 32768 字节与烧录前一致。

此前逐帧打印阶段的实板记录：采用每帧 10 ms 等待，向 F407 发送 `cmd`，F103 返回 `hyf OK, enter send update` 和 `CAN send done,len:4480`。当时 F407 串口共打印 560 帧、4480 字节，按顺序拼接后与原 BIN 逐字节完全一致。验证日志为本机 `MDK-ARM/can_frame_delay_live_20261031.log`，统计为同名 JSON；原始日志未纳入 Git。此结果属于逐帧打印版本，不代表当前长度统计版本进行了内容校验，也不涉及 W25Q16 保存或 EEPROM 标记。

当前长度统计版本的用户回传串口日志显示 `CAN receive done,len:4480`，与联调 BIN 长度一致；该结果只验证接收字节数，尚未校验内容、顺序或重复帧。
