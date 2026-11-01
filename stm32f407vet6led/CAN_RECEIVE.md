# F407 更新状态机

`application/app_update.c/.h` 管理更新流程，默认状态为 `APP_UPDATE_IDLE = 0`。主循环调用 `app_update_work()`。

| 状态 | 当前行为 |
| --- | --- |
| `APP_UPDATE_IDLE` | 每 500 ms 切换 LED0/LED1，等待串口中断切换状态 |
| `APP_UPDATE_SEND_COMMAND` | 发送一次标准 CAN ID `0`、DLC `3`、ASCII `hyf`，随后进入接收状态 |
| `APP_UPDATE_RECEIVE` | 接收标准 ID `1` 的 BIN 帧，按 DLC 追加到 5 KiB 缓冲区；连续 1 秒无有效数据后等待 CRC |
| `APP_UPDATE_VERIFY` | 等待独立 CRC 帧；匹配后保存 BIN、元数据和 EEPROM 更新状态；CRC 错误或超时清空本轮数据并回到空闲 |
| `APP_UPDATE_DONE` | BIN 和元数据已保存，EEPROM 更新状态和密钥读回一致，停留在完成状态 |
| `APP_UPDATE_ERROR` | 接收异常、镜像长度不符、Flash 擦写或 EEPROM 状态确认失败，停止本轮 |

仅空闲状态闪灯。合法串口命令触发一次 CAN 发送后开始接收，BIN 空闲 1 秒后进入 `APP_UPDATE_VERIFY` 等待 CRC；CRC 匹配且保存成功后进入 `APP_UPDATE_DONE`，保留 BIN。CRC 不匹配或最后一帧 BIN 后 2 秒仍未收到合法 CRC 时，清空 `can_rx_buffer`、将 `can_rx_len` 置 0，最后回到 `APP_UPDATE_IDLE`，恢复闪灯并允许再次发送 `cmd`。完成状态或错误状态需复位后开始下一轮；F103 重发前也需复位回到等待命令状态。

## 串口命令

USART1 为 PA9/TX、PA10/RX，115200、8N1。硬件初始化后调用 `app_update_init()`，使用 `HAL_UARTEx_ReceiveToIdle_IT()` 接收整条命令。回调中用 `strcmp()` 判断 `cmd` 并切换为发送状态。主循环在空闲状态调用闪灯函数，在发送状态直接调用 `int_can_send()`。

向 F407 发送 `cmd`，以串口 IDLE 结束，可附加 CR/LF；仅空闲状态可接受命令。无需附带长度，原来的 `start:长度` 和 `cmd:长度` 均不再作为启动命令。F103 装载 BIN 的串口命令仍为 `start:实际BIN长度`。

`app_update_state` 可在调试器中查看。`can_rx_len` 为本轮实际保存的 BIN 字节数；`can_rx_buffer[5120]` 只保存连续接收的 BIN，不包含末尾 CRC。两者定义于 `app_update.c`，在 `app_update.h` 声明，均由主循环更新。

## 接收与长度累计

发送 `hyf` 前清零 `can_rx_len` 和 5 KiB 缓冲区。接收状态通过 `int_can_receive_msg()` 轮询 FIFO0，只处理标准 ID `1`、数据帧、DLC 为 1～8 的报文。

应用接收接口为 `app_update_receive(void)`，接收状态直接调用 `app_update_receive()`，无需传入参数。

接收报文结构体为全局变量 `CAN_MSG_RECEIVE can_rx_msg`，定义于 `app_update.c`，在 `app_update.h` 中用 `extern` 声明，包含帧头、`data[8]` 和 `uint8_t count`。接收函数调用 `int_can_receive_msg(&can_rx_msg, &can_rx_msg.count)`，使用全局结构体成员保存读取前的 FIFO0 报文数。该底层接口一次只读一帧，数量大于 0 时处理本次读出的这一帧，FIFO 剩余报文留给下一次轮询；没有数据或读取失败时 `can_rx_msg.count` 为 0，仍检查结束时间。

函数将有效帧的实际 DLC 个字节追加到 `can_rx_buffer + can_rx_len`，再累加 `can_rx_len`，短尾帧按实际长度处理，越界检查使用 5 KiB 缓冲区的实际容量。无关 ID、扩展帧、远程帧、零长度或非法 DLC 不计数，也不刷新接收时间。

F103 在最后一帧 BIN 成功后等待 1100 ms 再发送 CRC。F407 连续 1000 ms 未收到有效数据时结束 BIN 接收：已有数据则打印一次 `CAN receive done,len:实际BIN字节数`，进入 `APP_UPDATE_VERIFY` 等待 CRC；完全没有有效数据则进入 `APP_UPDATE_ERROR`。读取 FIFO 前先检查 BIN 空闲时间，避免 CRC 已排队时被追加到 BIN。恰好 5120 字节允许正常完成；BIN 阶段下一帧若超出剩余空间，整帧不追加，保留已有数据和长度并进入错误状态。

## 末尾 CRC 与比较

接收顺序为 `[BIN] → 空闲 1100 ms → [独立的 4 字节小端 CRC 帧]`。例如 CRC 数值 `0x12345678` 的帧内容为 `78 56 34 12`。CRC 只覆盖缓冲区偏移 0 开始的完整 BIN，长度为 `can_rx_len`。CRC 帧不写入 BIN 缓冲区，也不增加长度；匹配时保留 BIN，失败时清空。

沿用当前 `app_crc_cal()` 的 STM32F407 硬件 CRC 方式：按小端内存顺序读取 32 位字，使用固定多项式 `0x04C11DB7`、初值 `0xFFFFFFFF`，不做输入/输出位反转或最终异或。BIN 末尾不足 4 字节时补 0 后计算；补 0 不增加 `can_rx_len`。缓冲区显式按 4 字节对齐，BIN 最大为 5120 字节。硬件说明见 [ST RM0090 的 CRC 章节](https://www.st.com.cn/resource/en/reference_manual/rm0090-stm32f405415-stm32f407417-stm32f427437-and-stm32f429439-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)。

校验状态只接受标准 ID `1`、数据帧、DLC `4` 的 CRC，其他报文不参与校验，也不延长等待时间。收到 CRC 并匹配时，通过 USART1 打印一次 `CRC OK\r\n`，随后执行外置 Flash 和 EEPROM 保存；不匹配则打印一次 `CRC FAIL\r\n`，清空缓冲区和长度后回到 `APP_UPDATE_IDLE`。无合法 CRC 时，从最后一帧 BIN 开始累计满 2000 ms 后同样打印失败、清空并回到空闲；该时间由 `APP_UPDATE_CRC_TIMEOUT_MS` 定义。短 BIN 仍可校验 CRC，但保存阶段要求长度大于 2048 字节，与主 Bootloader 的元数据条件一致。

配套 F103 自动对 Flash 中的原始 BIN 计算 CRC，先按最多 8 字节一帧发送 BIN，相邻 BIN 帧间隔 10 ms；最后一帧 BIN 成功后等待 1100 ms，再用标准 ID `1`、DLC `4` 发送小端 CRC。装载文件无需提前附加 CRC。4480 字节 BIN 对应 560 个满 8 字节 BIN 帧和最后 1 个 4 字节 CRC 帧，线上共 4484 字节；F407 的 `can_rx_len` 保持 4480。末尾不足 4 字节的 BIN 仅在 CRC 计算时补 0，不发送补齐字节。

日志只保留流程和接收字节数。以 4480 字节 BIN 为例：

```text
Waiting update command: cmd
Update receive start
CAN receive done,len:4480
CRC OK
Update save OK
```

接收数每轮只打印一次，不含 CRC，不逐帧打印，也不打印波特率启动信息。保存失败打印 `Update save FAIL` 并进入错误状态；CRC 错误、缺失、接收溢出或长度不大于 2048 字节时，不擦写 W25Q16，也不提交 EEPROM 更新状态。

## 外置 Flash 与 EEPROM

CRC 通过且长度为 `2049～5120` 字节时，在主循环中执行保存。先擦除元数据所在的第 0 个 4 KiB 扇区，再擦除 BIN 实际覆盖的扇区。`APP_UPDATE_W25_START_ADDR` 定义 BIN 起点为 `0x001000`，与主 Bootloader 的 `APP_W25_START_ADDR_MIN` 一致。

BIN 使用现有 `int_w25q16_write()` 写入，底层按 256 字节页边界拆分，每页写使能并等待 BUSY 清零；最后不足一页只写实际字节。CRC 帧和 CRC 计算补齐字节均不写入 Flash。

BIN 完整写入后，再向 W25Q16 `0x000000～0x000007` 写入两个小端 32 位数：BIN 片内起始地址和实际长度。4480 字节 BIN 对应元数据 `00 10 00 00 80 11 00 00`。Flash 阶段失败时停止，不提交 EEPROM 状态。

最后向 EEPROM `0x10～0x12` 写入 `01 5A 6B`：`0x01` 为 `BOOT_UPDATE`，`5A 6B` 为更新密钥 `0x5A6B`。读回三个字节一致后才打印 `Update save OK` 并进入完成状态；不一致进入错误状态。完成状态不重复擦写，也不自动复位。

下次复位后，主 Bootloader 根据 `BOOT_UPDATE` 搬运固件。搬运成功后仅将 EEPROM `0x10` 改为 `0x02`（`BOOT_NO_UPDATE`），保留 `0x11～0x12` 的密钥；搬运失败则保留更新标志并进入恢复流程。

## CAN 接口与接线

`int_can_init()` 配置全接收过滤器并启动 CAN1；`int_can_send()` 保留标准帧发送功能；`int_can_receive_msg()` 每次读取 FIFO0 中的一帧，供应用接收状态调用。

SPI1、CRC 和 I2C1 均在 APP 中初始化；W25Q16 使用 SPI1，EEPROM 使用 I2C1 的 PB6/SCL、PB7/SDA。

F407 CAN1 使用 PB8/RX、PB9/TX，正常模式、200 kbit/s：42 MHz / 42 / (1 + 2 + 2)。各 MCU 通过 CAN 收发器接入 CANH/CANL，共地，总线两端各接一个 120 Ω 终端电阻。

发送函数沿用现有 CAN 邮箱接口，当前状态切换只表示已调用发送函数，不代表对端已接收、固件已写入或更新完成。

F407 APP 起始地址为 `0x08008000`，通过原 Bootloader 启动；原始 BIN 应烧录到 `0x08008000`。

## 工程

Keil 工程：`MDK-ARM/stm32f407vet6led.uvprojx`。编译通过不代表实板串口与 CAN 已验证。

主机回归：`python tools/test_update_crc.py`，编译真实接收应用与 W25Q16 驱动，检查 CRC 时序、短 BIN 拒绝保存、256 字节页边界、4 KiB 扇区边界、5 KiB 容量、BIN/元数据内容、EEPROM `01 5A 6B` 提交，以及逐次 Flash 操作失败、EEPROM 写入或读回失败；原有无关帧、CRC 错误/缺失和溢出检查保留。
