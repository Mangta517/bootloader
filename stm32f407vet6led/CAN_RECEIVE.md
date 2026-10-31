# F407 通过 CAN 向 F103 发送 hyf

两边均为正常模式、200 kbit/s、标准数据帧，使用现有 CAN 引脚和时钟配置。

| 工程 | CAN_RX | CAN_TX | CAN 时钟与位时序 |
| --- | --- | --- | --- |
| STM32F103CAN | PA11 | PA12 | 36 MHz / 36 / (1 + 2 + 2) = 200 kbit/s |
| stm32f407vet6led | PB8 | PB9 | 42 MHz / 42 / (1 + 2 + 2) = 200 kbit/s |

## 收发流程

- F407 上电约一秒后，每秒主动发送一次标准数据帧：ID `0`、DLC `3`、数据 `68 79 66`（ASCII `hyf`）。
- 命令不带回车、换行或字符串结束符，不填充成 8 字节；发送不依赖 F103 先发报文。
- F407 应用层已移除接收报文打印和原样回包流程，保留 LED 闪烁。
- F103 硬件过滤器只接收标准 ID `0` 的数据帧；`app_update_wait()` 校验长度为 3、内容为 `hyf` 后，从等待状态切换到发送状态。
- F103 的 `app_update_send()` 当前为空函数，收到命令后尚不发送更新内容。

提交 CAN 邮箱不代表 F103 已处理命令；F103 串口出现 `hyf OK, enter send update` 才表示命令校验通过。

## 接线与查看

每块 MCU 的 CAN_TX 接各自收发器 TXD，CAN_RX 接 RXD。两个收发器的 CANH 相连、CANL 相连，GND 共地；总线两端各接一个 120 Ω 终端电阻。MCU 的 CAN 引脚需要经过 CAN 收发器接入总线。

查看 F103 命令接收结果：将 **F103 PA9/TX** 接 3.3 V USB 转 TTL 的 RXD，GND 接 GND；串口助手设为 **115200、8N1、无流控**。收到合法命令后打印：

```text
hyf OK, enter send update
```

F407 的 USART1 仅保留上电提示 `CAN1 TX hyf 200000 bit/s`，不再打印接收报文。F407 APP 起始地址仍为 `0x08008000`，通过原 Bootloader 启动；HEX 自带地址，原始 BIN 应烧录到 `0x08008000`。

## 工程与验证

- F103：`D:/3251/stm32f407vet6/STM32F103CAN/MDK-ARM/STM32F103CAN.uvprojx`。
- F407：`D:/3251/stm32f407vet6/stm32f407vet6led/MDK-ARM/stm32f407vet6led.uvprojx`。
- F407 主机回归：在 F407 工程目录运行 `python tools/test_can_receive.py`，检查发送 ID、三字节命令、1000 ms 间隔、毫秒计数回绕、应用无串口输出，以及 CAN 接口的接收和初始化错误处理。
- F103 主机回归：在 F103 工程目录运行 `python tools/test_app_update.py`，检查 `hyf` 命令校验和状态切换。
- 本次两套主机回归通过；F407 Keil 编译 0 错误、0 警告，日志为 `MDK-ARM/can_hyf_build_20261030.log`。尚未烧录或验证实板收发。
