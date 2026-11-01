# 单 LED 闪烁更新测试 APP

适用于 STM32F407VET6，仅让板载 LED0（PE5，低电平点亮）每 500 ms 翻转一次。使用 CMSIS、HSI 16 MHz 和 SysTick，不包含 CAN、串口、Flash 或 EEPROM 业务。

- APP 链接地址及向量表地址：`0x08008000`。
- Keil 工程：`MDK-ARM/stm32f407vet6led_blink.uvprojx`，沿用 ARM Compiler 5。
- 更新文件：`MDK-ARM/stm32f407vet6led_blink/stm32f407vet6led_blink.bin`，固定为 4480 字节。
- `.raw` 是编译器生成的未补齐镜像；更新时使用 `.bin`。

Keil 构建后先使用 FromELF 导出原始镜像，再运行 `tools/make_update_bin.py` 检查向量并以 `0xFF` 补齐到 4480 字节。构建后处理沿用本机 FromELF 和 Python 路径，换机时需调整。

把 `.bin` 装载到 F103 的 BIN 数据区 `0x08008000` 后，使用直接发送入口；其固定长度宏为 4480，无需修改。复位 F103 并看到 `Waiting update command: hyf` 后，向 F407 更新 APP 发送 `cmd`。F103 会自行计算 CRC 并通过 CAN 发送 4480 字节。F407 将 BIN 保存到 W25Q16 `0x001000`，元数据为 `00 10 00 00 80 11 00 00`，EEPROM 更新状态及密钥为 `01 5A 6B`。

复位 F407 后，主 Bootloader 搬运 APP，并把 EEPROM 更新标志改为 `0x02`，随后运行本测试 APP。也可以将本工程的 HEX 直接烧录到 F407 的 APP 分区测试闪灯。

本 APP 只用于检查更新后的启动和 LED 闪烁，运行后没有 CAN 更新接收功能。需要再次更新时，使用串口恢复程序，或重新部署原来的更新 APP。
