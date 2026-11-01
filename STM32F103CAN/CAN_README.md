# F103 更新应用

`Core/Src/main.c` 复位后直接使用 Flash `0x08008000` 中已装载的 4480 字节 BIN，设置 `APP_FLASH_DONE` 后调用 `app_update_init()`，等待 CAN 更新命令；不重新接收或擦除 BIN 数据区。

长度由 `appliaction/app_update.h` 中的 `APP_UPDATE_STORED_SIZE_BYTES = 4480` 定义；更换已存 BIN 后需要同步修改该宏，不自动探测文件长度。

启动后串口提示 `Waiting update command: hyf`。向 F407 串口发送 `cmd`，F407 发出 ID 0 的三字节 `hyf`；F103 收到命令后，以 ID 1 分帧发送已有 BIN，最后一帧 BIN 成功后等待 1100 ms，再发送 4 字节小端 CRC。成功进入 `APP_UPDATE_DONE`，失败进入 `APP_UPDATE_ERROR`；重新测试时复位 F103，并让 F407 回到空闲状态后再发送 `cmd`。

串口接收功能仍保留。需要重新装载 BIN 时，将入口恢复为 `App_bootloader_init()`、循环调用 `App_bootloader_update()` 直到 `APP_FLASH_DONE`，最后调用 `app_update_init()`。具体串口操作见 `FLASH_RECEIVE.md`。

## CAN 接口

自定义功能函数位于 `interface/int_can.c` 和 `.h`，应用层包含 `int_can.h`：

- `can_init()`：配置过滤器并启动 CAN，调用前完成 `MX_CAN_Init()`。
- `can_send(id, data, len)`：发送标准数据帧，等待硬件完成并检查 TXOK，返回 0 表示总线发送成功、1 表示失败。邮箱满时立即失败，已提交帧最多等待 100 ms，超时请求撤销；当前自动重传关闭，失败不继续发送后续固件。
- `can_receive(header, data)`：轮询 FIFO0，返回 0 表示收到一帧、1 表示无报文或读取失败。

`can_send()` 的缓冲区必须有 8 字节，`len` 为 0～8；HAL 装载邮箱时读取整个 8 字节缓冲区，实际上总线只发送 DLC 指定的字节数。CubeMX 的外设初始化仍位于 `Core`。

## 命令

F407 发送标准数据帧：**ID `0`、DLC `3`、数据 `68 79 66`（ASCII `hyf`）**。大小写、长度和内容必须完全匹配，不带回车、换行或零填充。

F103 的硬件过滤器只接收标准 ID `0` 的数据帧。发送格式如下：

- 数据帧：均为标准 ID `1`；先发 BIN，普通帧 DLC 为 8，尾帧 DLC 为剩余 1～8 字节；BIN 发完后等待 1100 ms，再发一帧 DLC 为 4 的小端 CRC。
- 数据内容：`[Flash 中原始 BIN] → 空闲 1100 ms → [4 字节小端 CRC]`，无序号或长度帧，也不发送奇数字节写 Flash 时补齐的 FF。Flash 中只需保存原始 BIN，无需提前添加 CRC。
- CRC：只覆盖有效 BIN，使用 STM32 硬件 CRC，固定多项式 `0x04C11DB7`、初值 `0xFFFFFFFF`，不做位反转或最终异或。完整 32 位字直接从 Flash 按小端读取，末尾不足 4 字节时在 RAM 中补 0 并继续累积计算；不改写 Flash，补 0 不发送。
- 发送循环：先计算 BIN 的 CRC，再从 `app_size_bytes` 开始发送 BIN；够 8 字节就发送 8 字节，不足 8 字节则发送尾包。只有发送成功才扣减剩余长度并推进 Flash 偏移；全部 BIN 成功后才等待并发送 CRC。一次 `app_update_work()` 在发送状态下完成整轮流程。
- 发送等待：BIN 相邻帧间隔 10 ms，由 `APP_UPDATE_FRAME_DELAY_MS` 定义；最后一帧 BIN 成功后单独等待 `APP_UPDATE_CRC_DELAY_MS = 1100`，不额外叠加 10 ms，CRC 发送后不再等待。
- 总长度：BIN 为当前启动设置的 `app_size_bytes`，发送端支持 1～32768 字节，线上总长度为 `app_size_bytes + 4`。当前 F407 的 5120 字节缓冲区只保存 BIN，因此双板 CRC 联调的 BIN 上限为 5120 字节。

例如 9 字节 BIN 先发送 DLC=8 和 DLC=1 两帧，等待 1100 ms 后发送 DLC=4 的 CRC。CRC 发送完成后打印有效 BIN 长度 `CAN send done,len:9`，不含 CRC；若 CRC 发送失败，打印 `CAN send CRC error` 并停止；若 1 字节 BIN 尾帧失败，则打印 `CAN send error, offset:8` 并停止，不再发送 CRC。DONE/ERROR 状态不再响应周期 `hyf`；开始新一轮需复位 F103，无需重新装载 BIN。

以已装载的 4480 字节 BIN 为例，CAN 共发送 561 帧、4484 字节：先发 560 帧 DLC=8 的 BIN，再等 1100 ms，最后发 1 帧 DLC=4 的 CRC。BIN 帧间 559 次 10 ms 加 CRC 前 1100 ms，仅等待时间合计 6.69 秒，另有 CRC 计算、总线和日志开销。完成打印 `CAN send done,len:4480`。

F407 收到串口 `cmd` 后发送一次 `hyf`，打印 `Update receive start`，随后接收 BIN；连续 1 秒无有效数据后打印实际长度并等待 CRC。CRC 匹配且长度为 `2049～5120` 字节时，按页保存 W25Q16 正文和元数据，并提交 EEPROM `01 5A 6B`，全部成功后打印 `Update save OK`。CRC 错误或缺失时不保存；保存失败打印 `Update save FAIL`。详细流程见配套 F407 的 `CAN_RECEIVE.md`；F103 的 TXOK 仅表示 CAN 帧被确认。

主循环只调用 `app_update_work()`，状态机放在应用层；原来每秒发送测试帧的流程已移除。F407 每次从空闲状态接受 `cmd` 后发送一次 ID `0`、DLC `3` 的 `hyf` 命令。

## 引脚和打印

- CAN：200 kbit/s，F103 PA11/RX、PA12/TX；F407 当前工程使用 PB8/RX、PB9/TX。各自经 CAN 收发器接入 CANH/CANL，共地，总线两端各一个 120 Ω 终端。
- F103 串口：USART1 **PA9/TX** 接 3.3 V USB 转 TTL 的 RXD，**PA10/RX** 接 TXD，GND 共地，**115200、8N1、无流控**。更新命令走 CAN，串口支持打印和循环 DMA 接收，接收接口见 `UART_RECEIVE.md`。

应用层统一使用 `printf()`；`Core/Src/usart.c` 中的 `fputc()` 将字符输出到 USART1，Keil 工程启用 MicroLIB。

启动提示：

```text
Waiting update command: hyf
```

更新命令匹配：

```text
Update send start
```

发送完成：

```text
CAN send done,len:实际BIN字节数
```

仅保留流程、完成字节数和更新失败提示，不打印波特率启动信息或无效命令回显。F407 对应输出为等待 `cmd`、开始接收、BIN 接收字节数和 CRC 结果；接收数每轮只输出一次，不含 CRC 的 4 字节。

## 验证

主机回归：在当前工程目录运行 `python tools/test_app_update.py`，编译真实应用层与 CAN 驱动，检查启动顺序、严格命令校验、Flash 状态/长度、先 BIN 后 CRC、最后一帧 BIN 成功到 CRC 提交之间的 1100 ms 等待、独立 CRC 参考值、1～3 字节尾部补 0、Flash 不变、逐字节分帧、32 KiB 边界、三邮箱、tick 回绕，以及首帧/BIN/CRC 发送失败时停止。F407 接收回归为配套工程的 `python tools/test_update_crc.py`。

串口接收及写 Flash 联合测试：`python tools/test_app_flash.py`。

Keil 工程为 `MDK-ARM/STM32F103CAN.uvprojx`。2026-10-31 修复后两套 F103 回归通过，Keil 编译 0 错误、0 警告。已通过 ST-Link 写入并读回核对程序，BIN 数据区全部 32768 字节与烧录前一致。

2026-11-01 此前 CRC 包头版本通过主机回归与 Keil 编译；随后改为先 BIN、等待 1100 ms、再 CRC，并同步调整 F407 接收流程。用户回传的当前版本串口日志包含 `CAN receive done,len:4480`、`CRC OK` 和 `Update save OK`，确认该轮接收长度、CRC 和保存成功提示。

当前末尾 CRC 版本：F103 `test_app_update.py` 与 F407 `test_update_crc.py` 均通过；两端 Keil APP 工程均为 0 错误、0 警告。主引导拒绝 `0x1000` 起点的问题在提示同步更新板上 Bootloader 后，用户已确认解决；没有新增烧录读回或 EEPROM `0x02` 的独立测量记录。1100 ms 等待已有主机断言，实板总线时序未单独测量。

此前逐帧打印阶段的实板记录：采用每帧 10 ms 等待，向 F407 发送 `cmd`，F103 返回 `hyf OK, enter send update` 和 `CAN send done,len:4480`。当时 F407 串口共打印 560 帧、4480 字节，按顺序拼接后与原 BIN 逐字节完全一致。验证日志为本机 `MDK-ARM/can_frame_delay_live_20261031.log`，统计为同名 JSON；原始日志未纳入 Git。此结果属于逐帧打印版本，不代表当前长度统计版本进行了内容校验，也不涉及 W25Q16 保存或 EEPROM 标记。

此前长度统计版本的用户回传串口日志显示 `CAN receive done,len:4480`，与联调 BIN 长度一致；该历史结果只验证接收字节数，不代表本次 CRC 包头版本已完成实板验证。
