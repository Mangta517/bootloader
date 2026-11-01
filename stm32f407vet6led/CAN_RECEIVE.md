# F407 更新状态机

`application/app_update.c/.h` 管理更新流程，默认状态为 `APP_UPDATE_IDLE = 0`。主循环调用 `app_update_work()`。

| 状态 | 当前行为 |
| --- | --- |
| `APP_UPDATE_IDLE` | 每 500 ms 切换 LED0/LED1，等待串口中断切换状态 |
| `APP_UPDATE_SEND_COMMAND` | 发送一次标准 CAN ID `0`、DLC `3`、ASCII `hyf`，随后进入接收状态 |
| `APP_UPDATE_RECEIVE` | 接收标准 ID `1` 数据帧，按 DLC 追加到 5 KiB 缓冲区并累计长度；连续 1 秒无有效数据后进入校验状态 |
| `APP_UPDATE_VERIFY` | 读取前 4 字节的小端 CRC，仅对后续有效载荷计算硬件 CRC；失败时清空本轮数据并回到空闲 |
| `APP_UPDATE_DONE` | CRC 匹配并打印一次通过消息，停留在完成状态，后续更新处理待实现 |
| `APP_UPDATE_ERROR` | 未收到有效数据或接收长度超过缓冲区容量，停止本轮 |

仅空闲状态闪灯。合法串口命令触发一次 CAN 发送后开始接收，接收结束后进入 `APP_UPDATE_VERIFY`；CRC 匹配进入 `APP_UPDATE_DONE`，保留数据。CRC 不匹配或包头后没有有效载荷时，清空 `can_rx_buffer`、将 `can_rx_len` 置 0，最后回到 `APP_UPDATE_IDLE`，恢复闪灯并允许再次发送 `cmd`。完成状态或接收错误状态仍需复位后开始下一轮；F103 重发前也需重新准备好数据。

## 串口命令

USART1 为 PA9/TX、PA10/RX，115200、8N1。硬件初始化后调用 `app_update_init()`，使用 `HAL_UARTEx_ReceiveToIdle_IT()` 接收整条命令。回调中用 `strcmp()` 判断 `cmd` 并切换为发送状态。主循环在空闲状态调用闪灯函数，在发送状态直接调用 `int_can_send()`。

向 F407 发送 `cmd`，以串口 IDLE 结束，可附加 CR/LF；仅空闲状态可接受命令。无需附带长度，原来的 `start:长度` 和 `cmd:长度` 均不再作为启动命令。F103 装载 BIN 的串口命令仍为 `start:实际BIN长度`。

`app_update_state` 可在调试器中查看。`can_rx_len` 为本轮实际保存的总字节数，包含前 4 字节 CRC 包头；`can_rx_buffer[5120]` 保存包头和连续接收的数据。两者定义于 `app_update.c`，在 `app_update.h` 声明，均由主循环更新。

## 接收与长度累计

发送 `hyf` 前清零 `can_rx_len` 和 5 KiB 缓冲区。接收状态通过 `int_can_receive_msg()` 轮询 FIFO0，只处理标准 ID `1`、数据帧、DLC 为 1～8 的报文。

应用接收接口为 `app_update_receive(void)`，接收状态直接调用 `app_update_receive()`，无需传入参数。

接收报文结构体为全局变量 `CAN_MSG_RECEIVE can_rx_msg`，定义于 `app_update.c`，在 `app_update.h` 中用 `extern` 声明，包含帧头、`data[8]` 和 `uint8_t count`。接收函数调用 `int_can_receive_msg(&can_rx_msg, &can_rx_msg.count)`，使用全局结构体成员保存读取前的 FIFO0 报文数。该底层接口一次只读一帧，数量大于 0 时处理本次读出的这一帧，FIFO 剩余报文留给下一次轮询；没有数据或读取失败时 `can_rx_msg.count` 为 0，仍检查结束时间。

函数将有效帧的实际 DLC 个字节追加到 `can_rx_buffer + can_rx_len`，再累加 `can_rx_len`，短尾帧按实际长度处理，越界检查使用 5 KiB 缓冲区的实际容量。无关 ID、扩展帧、远程帧、零长度或非法 DLC 不计数，也不刷新接收时间。

F103 没有结束帧，因此连续 1000 ms 未收到有效数据时结束本轮：已有数据则进入 `APP_UPDATE_VERIFY`；完全没有有效数据则进入 `APP_UPDATE_ERROR`。恰好 5120 字节允许正常完成；下一帧若超出剩余空间，整帧不追加，保留已有数据和长度并进入错误状态。

## CRC 包头与比较

接收格式为 `[4 字节小端 CRC][有效载荷]`。例如 CRC 数值 `0x12345678` 的包头字节为 `78 56 34 12`。CRC 只覆盖偏移 4 开始的有效载荷，长度为 `can_rx_len - 4`；包头不参与 CRC 计算。匹配时保留原始缓冲区和总长度，失败时清空。

沿用当前 `app_crc_cal()` 的 STM32F407 硬件 CRC 方式：按小端内存顺序读取 32 位字，使用固定多项式 `0x04C11DB7`、初值 `0xFFFFFFFF`，不做输入/输出位反转或最终异或。有效载荷末尾不足 4 字节时补 0 后计算；补 0 不增加 `can_rx_len`。缓冲区显式按 4 字节对齐，包头占用 4 字节，因此有效载荷最大为 5116 字节。硬件说明见 [ST RM0090 的 CRC 章节](https://www.st.com.cn/resource/en/reference_manual/rm0090-stm32f405415-stm32f407417-stm32f427437-and-stm32f429439-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)。

总长度不超过 4 字节时直接清空本轮数据并回到空闲；完整包计算出的 CRC 与包头值相等时，通过 USART1 打印一次 `CRC校验通过\r\n`，然后进入 `APP_UPDATE_DONE`。不相等则清空缓冲区和长度后回到 `APP_UPDATE_IDLE`，不打印通过消息。

本地 F103 代码目前原样发送装载的文件，没有额外生成 CRC 包头；装载文件需已包含上述包头，且按相同 CRC 算法生成。例如 4480 字节有效载荷加 4 字节包头后，总长度为 4484 字节，共 560 个满 8 字节帧和 1 个 4 字节尾帧。旧的不带 CRC 包头的原始 BIN 不能直接用于此校验协议。

接收过程不逐帧打印，CRC 校验通过后打印一次通过消息，不调用 W25Q16 擦除、写入，也不操作 EEPROM。

## CAN 接口与接线

`int_can_init()` 配置全接收过滤器并启动 CAN1；`int_can_send()` 保留标准帧发送功能；`int_can_receive_msg()` 每次读取 FIFO0 中的一帧，供应用接收状态调用。

底层 W25Q16 驱动、SPI1 和 CRC 外设配置保留，当前更新状态机不执行 Flash 擦除或写入。

F407 CAN1 使用 PB8/RX、PB9/TX，正常模式、200 kbit/s：42 MHz / 42 / (1 + 2 + 2)。各 MCU 通过 CAN 收发器接入 CANH/CANL，共地，总线两端各接一个 120 Ω 终端电阻。

发送函数沿用现有 CAN 邮箱接口，当前状态切换只表示已调用发送函数，不代表对端已接收、固件已写入或更新完成。

F407 APP 起始地址为 `0x08008000`，通过原 Bootloader 启动；原始 BIN 应烧录到 `0x08008000`。

## 工程

Keil 工程：`MDK-ARM/stm32f407vet6led.uvprojx`。编译通过不代表实板串口与 CAN 已验证。
