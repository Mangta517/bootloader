#ifndef APP_UPDATE_H
#define APP_UPDATE_H

#include <stdint.h>
#include "int_can.h"

/* 与主 Bootloader 的布局一致：第 0 扇区存 8 字节元数据，BIN 从第 1 扇区起存放。 */
#define APP_UPDATE_W25_START_ADDR 0x001000

typedef enum
{
    APP_UPDATE_IDLE = 0,       /* 空闲，闪灯并等待串口 cmd 命令。 */
    APP_UPDATE_SEND_COMMAND,   /* 发送一次 CAN 更新命令。 */
    APP_UPDATE_RECEIVE,        /* 接收 ID 1 BIN 到 5 KiB 缓冲区，空闲 1 秒后等待 CRC。 */
    APP_UPDATE_VERIFY,         /* 等待独立的 4 字节小端 CRC 帧并与 BIN 的硬件 CRC 比较。 */
    APP_UPDATE_DONE,           /* BIN、元数据和 EEPROM 更新状态、密钥均已保存。 */
    APP_UPDATE_ERROR           /* 接收异常、镜像长度不符、W25Q16 擦写或 EEPROM 状态确认失败。 */
} app_update_state_enum;

/* 串口 cmd 命令切换发送状态，主循环执行状态机。 */
extern volatile app_update_state_enum app_update_state;
/* 定义于 app_update.c，由主循环更新；缓冲区与 can_rx_len 只包含 BIN，不包含末尾 CRC。 */
extern uint8_t can_rx_buffer[5120];
extern uint32_t can_rx_len;
/* 定义于 app_update.c，保留主循环最近一次读出的报文和 FIFO 数量。 */
extern CAN_MSG_RECEIVE can_rx_msg;

/* USART1 硬件初始化后调用，启用空闲中断接收整条命令。 */
void app_update_init(void);
/* 内部调用 CAN 接收接口，将数据拼入 can_rx_buffer 并累计 can_rx_len。 */
void app_update_receive(void);
void app_update_check_crc(void);
void app_update_work(void);

#endif
