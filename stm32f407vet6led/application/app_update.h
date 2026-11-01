#ifndef APP_UPDATE_H
#define APP_UPDATE_H

#include <stdint.h>
#include "int_can.h"

typedef enum
{
    APP_UPDATE_IDLE = 0,       /* 空闲，闪灯并等待串口 cmd 命令。 */
    APP_UPDATE_SEND_COMMAND,   /* 发送一次 CAN 更新命令。 */
    APP_UPDATE_RECEIVE,        /* 接收 ID 1 数据到 5 KiB 缓冲区，空闲 1 秒后进入校验状态。 */
    APP_UPDATE_VERIFY,         /* 比较小端 CRC 包头与有效载荷的硬件 CRC。 */
    APP_UPDATE_DONE,           /* CRC 匹配，后续更新处理待实现。 */
    APP_UPDATE_ERROR           /* 未收到有效数据或接收长度超过缓冲区容量。 */
} app_update_state_enum;

/* 串口 cmd 命令切换发送状态，主循环执行状态机。 */
extern volatile app_update_state_enum app_update_state;
/* 定义于 app_update.c，由主循环更新；can_rx_len 为含 4 字节 CRC 包头的总字节数。 */
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
