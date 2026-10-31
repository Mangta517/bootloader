# F407 更新状态机

`application/app_update.c/.h` 管理更新流程，默认状态为 `APP_UPDATE_IDLE = 0`。主循环调用 `app_update_work()`。

| 状态 | 当前行为 |
| --- | --- |
| `APP_UPDATE_IDLE` | 每 500 ms 切换 LED0/LED1，等待串口中断切换状态 |
| `APP_UPDATE_SEND_COMMAND` | 发送一次标准 CAN ID `0`、DLC `3`、ASCII `hyf`，随后进入接收状态 |
| `APP_UPDATE_RECEIVE` | 累计标准 ID `1` 数据帧的 DLC；连续 1 秒无有效数据后打印总字节数，回到空闲状态 |
| `APP_UPDATE_VERIFY` | 预留，尚未实现固件校验 |
| `APP_UPDATE_DONE` | 预留，尚未修改 EEPROM 更新状态 |

仅空闲状态闪灯。上电不自动发送 CAN 命令；合法串口命令触发一次发送后开始接收统计，结束后回到空闲状态，不会进入固件校验或 EEPROM 更新流程。

## 串口命令

USART1 为 PA9/TX、PA10/RX，115200、8N1。硬件初始化后调用 `app_update_init()`，使用 `HAL_UARTEx_ReceiveToIdle_IT()` 接收整条命令。回调中用 `strcmp()` 判断 `cmd` 并切换为发送状态。主循环在空闲状态调用闪灯函数，在发送状态直接调用 `int_can_send()`。

向 F407 发送 `cmd`，以串口 IDLE 结束，可附加 CR/LF。无需附带长度，原来的 `start:长度` 和 `cmd:长度` 均不再作为启动命令。F103 装载 BIN 的串口命令仍为 `start:实际BIN长度`。

`app_update_state` 可在调试器中查看。`app_update_size_bytes` 保存本轮实际接收字节数，每次发送 `hyf` 前清零，统计结束后保留到下一轮。当前不接收预期长度。固件拼接、W25Q16 写入、镜像校验和 EEPROM 标志提交暂未实现。

## 接收丢包测试

先让 F103 准备好 BIN，再向 F407 串口发送 `cmd`。F407 发送一次 `hyf` 后进入接收状态，不再逐帧打印，避免阻塞串口输出影响接收速度。只累计标准 ID `1`、数据帧、DLC 为 1～8 的有效载荷字节数，短尾帧按实际 DLC 计数。

F103 当前没有长度帧或结束帧，因此连续 1000 ms 未收到有效数据视为本轮结束，通过同一 USART1 打印一次总字节数，例如：

```text
CAN receive done,len:4480
```

无关 ID、扩展帧、远程帧、零长度帧和非法 DLC 不计数，也不重置空闲计时。没有收到任何有效数据时，发送命令后等待 1 秒打印 `CAN receive done,len:0`。接收间隔如果达到 1 秒，会被当作本轮结束。

将 F407 的接收长度与 F103 的 `CAN send done,len:4480` 对照；当前测试 BIN 为 4480 字节，应收到 560 个 8 字节数据帧。少于 4480 表示本轮统计的字节不足，长度相等仅说明字节数匹配，不校验内容、顺序或重复帧。完成后 F407 回到空闲，可再次接受 `cmd`；F103 完成后停留在 DONE，开始下一轮前需复位 F103。

## CAN 接口与接线

`int_can_init()` 配置全接收过滤器并启动 CAN1；`int_can_send()` 保留标准帧发送功能；`int_can_receive_msg()` 保留 FIFO0 单帧读取功能，更新状态机在接收状态中连续轮询，只在本轮结束后通过 USART1 打印累计长度。

F407 CAN1 使用 PB8/RX、PB9/TX，正常模式、200 kbit/s：42 MHz / 42 / (1 + 2 + 2)。各 MCU 通过 CAN 收发器接入 CANH/CANL，共地，总线两端各接一个 120 Ω 终端电阻。

发送函数沿用现有 CAN 邮箱接口，当前状态切换只表示已调用发送函数，不代表对端已接收、固件已写入或更新完成。

F407 APP 起始地址为 `0x08008000`，通过原 Bootloader 启动；原始 BIN 应烧录到 `0x08008000`。

## 验证

运行 `python tools/test_can_receive.py`，覆盖串口命令解析、IDLE/CRLF 结束、非法命令、UART 错误、空闲闪灯、每轮单次 hyf、DLC 累计与短尾帧、无关帧过滤、1 秒空闲结束、零字节报告、多轮清零、tick 回绕、4480 字节接收统计、预留状态及 CAN FIFO/初始化错误处理。

Keil 工程：`MDK-ARM/stm32f407vet6led.uvprojx`。主机回归及编译不代表实板串口与 CAN 已验证。

当前长度统计版本已收到用户实板串口反馈：发送 `cmd` 后打印 `CAN receive done,len:4480`，与当前联调 BIN 长度一致。本轮按字节数统计未发现缺失；没有进行数据内容、顺序或重复帧校验。W25Q16 接收保存、CAN 接收中断和 1 KiB 数据缓冲仍未实现，缓冲容量讨论不属于本次代码改动。
