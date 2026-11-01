# F103 串口接收并写入内部 Flash

`Core/Src/main.c` 当前直接使用 Flash 中已有的 4480 字节 BIN，复位后等待 CAN 更新命令。下面说明保留的串口接收功能；需要重新装载 BIN 时，先恢复串口接收入口。

## 分区

- 程序：`0x08000000～0x08007FFF`，32 KiB，Keil 两处 IROM 配置和链接范围已限制到此前半区。
- BIN 数据：`0x08008000～0x0800FFFF`，32 KiB。
- 地址与容量定义在 `appliaction/app_bootloader.h`。本功能存储 BIN，不执行跳转。

## 发送步骤

USART1：PA9/TX、PA10/RX，3.3 V TTL，共地，115200、8N1。

1. 文本发送 `start:实际BIN字节数`，例如单 LED 测试 BIN 使用 `start:4480`，可附加 CR/LF；沿用原工程的串口 IDLE 命令结束方式。
启动只提示 `Waiting BIN: start:len`，擦除完成后提示 `Flash erased; send data`；成功反馈沿用 `ok,len:长度`，保留原有 CR/LF 格式。不再打印无效命令、无效长度和重复的声明长度，解析与校验逻辑保留。

2. 声明长度必须为 1～32768 字节。应用按长度提前擦除目标 Flash 页，随后启动循环 DMA。
3. 等待串口返回 `Flash erased; send data`，再单独发送原始二进制 BIN 文件。命令和 BIN 不要连发。
4. 接收期间按最多 512 字节连续追加写入，跨块的奇数字节先保留。
5. 接收层确认空闲并取完尾部后等待 2 秒；新字节到达会重新计时。
6. 比较实际 BIN 长度与 `start:` 声明长度，匹配后补写最后一个奇数字节（高字节填 FF）、停止 DMA 接收，打印 `ok,len:长度`。
7. 只有上述流程成功后，更新状态机才开始判断 CAN 的 `hyf` 更新命令。收到合法命令后，以标准 ID `1` 将有效 BIN 按每帧最多 8 字节发出，尾部 FF 填充不发送；发送格式和完成/失败行为见 `CAN_README.md`。

Flash 使用 F103 的 1 KiB 页擦除和 16 位半字写入。有效长度不包含命令及最后的 FF 填充。
串口/DMA 错误、缓冲溢出、超出声明长度、2 秒后长度不符或 Flash 操作失败，均沿用原程序的错误打印及复位流程，不进入 CAN 更新判断。
本次接收成功后停止写入；开始新一轮请重新启动设备并发送新的 `start:长度`。

## 调试变量

- `app_flash_state`：WAIT 等命令、RECEIVING 接收中、DONE 成功、ERROR 失败后复位。
- `uart1_rx_app_len`：从 `start:` 解析出的预期 BIN 字节数。
- `app_size_bytes`：已接受的 BIN 字节数，DONE 时为有效写入长度；接收中的最后一个奇数字节可能仍在 RAM。
- `uart1_rx_full_len`：DMA 接收的 BIN 总字节数，不包含命令。

恢复串口接收入口时，先调用 `App_bootloader_init()`，再在独立 while 循环中持续调用 `App_bootloader_update()`，接收完成后才调用 `app_update_init()`。当前启动入口不执行上述接收循环，也不擦除 BIN 数据区。
原 `interface/int_uart.c` 的循环 DMA 接收处理逻辑未改动。USART1 的 `fputc()` 负责协议反馈与现有 printf 日志。

## 验证

- `python tools/test_app_flash.py`：实际 DMA 接收代码与应用层联合模拟，含命令解析、预擦除、2 秒收尾、长度不符、奇数尾部、32 KiB 边界及错误路径。
- `python tools/test_app_update.py`：检查已有 BIN 的直接 CAN 启动流程、`hyf` 命令校验和发送行为。
- Keil 工程：`MDK-ARM/STM32F103CAN.uvprojx`。

主机测试和固件构建不代替板上串口与 Flash 读回验证。
