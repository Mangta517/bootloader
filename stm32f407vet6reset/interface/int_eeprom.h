#ifndef INT_EEPROM_H
#define INT_EEPROM_H

#include <stdint.h>

/* 板载 U3（24C02）共 256 字节；原理图 A2/A1/A0 均接地，7 位地址为 0x50。 */
#define EEPROM_DEVICE_ADDR_7BIT 0x50
#define EEPROM_SIZE_BYTES       256
/* 按 8 字节边界拆分，也可用于页大小为 16 字节的 24C02。 */
#define EEPROM_PAGE_SIZE_BYTES 8

/*
 * 使用硬件 I2C1：PB6=SCL、PB7=SDA，需外部上拉；写入时 WP 必须为低电平。
 * 使用前先调用 CubeMX 的 MX_I2C1_Init()。
 * 以下接口为阻塞调用，只在主循环/任务中使用，调用期间需保持 HAL tick 运行。
 * 不检查通信结果；调用方负责提供有效数据缓冲区，并保证读取范围有效。
 */
/* 单字节读写，地址范围 0x00~0xFF。 */
uint8_t int_eeprom_read_byte(uint16_t address);
void int_eeprom_write_byte(uint16_t address, uint8_t data);

/* 地址范围 0x00~0xFF，length 单位为字节，必须大于 0，且不能超出芯片容量。 */
void int_eeprom_read(uint16_t address, uint8_t *data, uint16_t length);

/* 自动跨页写入，每页写入后固定等待 10 ms；超出容量的部分截断，起始地址越界则不写入。 */
void int_eeprom_write(uint16_t address, const uint8_t *data, uint16_t length);

#endif
