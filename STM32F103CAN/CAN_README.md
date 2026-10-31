# F103 更新应用

> 当前源码状态：`Core/Src/main.c` 的接收循环和 `app_update_init()` 入口已注释，以下为这些函数启用后的流程说明；启动顺序回归当前失败。

主函数按以下顺序运行：

1. 调用 `App_bootloader_init()`，然后在独立 `while` 中持续调用 `App_bootloader_update()`，接收 `start:长度` 和 BIN 并写 Flash。
2. 空闲 2 秒后完成长度校验和 Flash 收尾，只有 `app_flash_state == APP_FLASH_DONE` 才退出接收循环。
3. 调用 `app_update_init()` 启动 CAN 并设置 `APP_UPDATE_WAIT`，之后主循环只调用 `app_update_work()`。
4. `APP_UPDATE_WAIT` 严格校验 CAN 更新命令 `hyf`，合法后进入 `APP_UPDATE_SEND`；发送函数仍为空。

接收或写入失败时沿用原串口程序的错误打印和复位流程，不会进入 CAN 判断。串口流程详见 `FLASH_RECEIVE.md`。

## CAN 接口

自定义功能函数位于 `interface/int_can.c` 和 `.h`，应用层包含 `int_can.h`：

- `can_init()`：配置过滤器并启动 CAN，调用前完成 `MX_CAN_Init()`。
- `can_send(id, data, len)`：发送标准数据帧，返回 0 表示提交邮箱成功、1 表示失败。
- `can_receive(header, data)`：轮询 FIFO0，返回 0 表示收到一帧、1 表示无报文或读取失败。

以上函数从 `Core/Src/can.c` 原样迁移，逻辑和接口保持不变；CubeMX 的外设初始化仍位于 `Core`。

## 命令

F407 发送标准数据帧：**ID `0`、DLC `3`、数据 `68 79 66`（ASCII `hyf`）**。大小写、长度和内容必须完全匹配，不带回车、换行或零填充。

F103 的硬件过滤器只接收标准 ID `0` 的数据帧；后续发送仍可使用已有 `can_send(1, data, len)`。当前发送函数为空，收到命令后不会实际发送固件。

主循环只调用 `app_update_work()`，状态机放在应用层；原来每秒发送测试帧的流程已移除。F407 现每秒主动发送一次 ID `0`、DLC `3` 的 `hyf` 命令，不依赖 F103 先发报文。

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

## 验证

主机回归：在当前工程目录运行 `python tools/test_app_update.py`，检查初始化提示、无报文、错误 ID/帧类型/长度/内容、正确命令的状态切换和发送空函数。

串口接收及写 Flash 联合测试：`python tools/test_app_flash.py`。

Keil 工程为 `MDK-ARM/STM32F103CAN.uvprojx`。本次未烧录或做新的实板验证。
