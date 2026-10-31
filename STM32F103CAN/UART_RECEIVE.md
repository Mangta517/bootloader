# USART1 循环 DMA 接收

接收部分从 `../stm32f407vet6reset/interface/int_bootloader.c` 移植至
`interface/int_uart.c` / `.h`，保留循环 DMA、半区完成标记、累计字节数、
溢出计数和 10 ms 空闲尾包处理。原来的 Flash 写入、擦除、跳转不属于本模块。

- USART1：115200、8N1、无流控；PA9/TX、PA10/RX，使用 3.3 V TTL 并共地。
- RX DMA：DMA1 Channel5，循环模式，字节传输，半满/全满中断。
- DMA 缓冲 8192 字节，工作缓冲 512 字节；模块固定缓冲共占 8704 字节 RAM。
- 应用层先轮询解析 `start:长度` 并擦除目标页，再调用 `int_uart_init()` 启动 BIN 接收；返回 0 成功、1 失败。
- `int_uart_receive(data)` 由主循环调用，`data` 至少 512 字节，返回 0～512 的有效长度。
  返回的是二进制数据，不自动补 `\0`。每轮持续调用；返回 0 也可能是正在拼接跨半区数据。

底层取数调用形式（实际消费者已接入 `appliaction/app_bootloader.c`）：

```c
static uint8_t rx_data[UART1_RX_BUFFER_SIZE];
uint16_t rx_length;

rx_length = int_uart_receive(rx_data);
if (rx_length > 0)
{
    /* 在此使用 rx_data[0 .. rx_length - 1]。 */
}
```

当前由应用层接收并写入内部 Flash；空闲 2 秒且长度检查通过后才进入 CAN 更新命令判断，详见 `FLASH_RECEIVE.md`。
消费者应及时取数，避免 DMA 覆盖未读数据。连续接收时完成半区才开始取数；
不足半区的尾部在主循环观察到 DMA 位置稳定 10 ms 后取出。这是流式分块，不是协议帧边界。
在 115200、8N1 下，4096 字节约 356 ms，8192 字节约 711 ms。

诊断变量由 `int_uart.c` 定义：

| 变量 | 含义 |
| --- | --- |
| `uart1_rx_full_len` | 取数时累计观察到的接收字节数，包含已被覆盖的字节 |
| `uart1_rx_overflow_count` | 取数时发现的 DMA 覆盖字节数 |
| `uart1_rx_error_count` | 启动失败及 UART/DMA 错误次数；非零后停止取数 |
| `uart1_rx_complete_tick` | 空闲尾部已交给调用方的确认时间，单位 ms，新数据到达后清零 |

与原实现相同，DMA 回绕计数依赖中断及时执行；不能长时间屏蔽 DMA 中断。
出错后由上层决定后续动作，本模块不自动重新启动接收。

验证命令：`python tools/test_uart_receive.py`、`python tools/test_app_update.py`。
Keil 工程：`MDK-ARM/STM32F103CAN.uvprojx`，新增接口和 HAL DMA 源文件已加入构建。
CubeMX `.ioc` 已同步双向串口与 RX DMA 配置。
