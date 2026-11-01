#ifndef __INT_CAN_H__
#define  __INT_CAN_H__

#include <stdint.h>
#include "can.h"

typedef struct
{
    CAN_RxHeaderTypeDef RxHeader;
    uint8_t data[8];
    uint8_t count;    /* 本次读取前 FIFO0 的报文数量，0 表示无数据或读取失败。 */
} CAN_MSG_RECEIVE;
// 配置全接收过滤器并启动 CAN1。
void int_can_init(void);
//发送消息
void int_can_send(uint16_t id, uint8_t *data, uint8_t len);
// 每次接收一条消息，count 为读取前 FIFO0 中的报文数量。
void int_can_receive_msg(CAN_MSG_RECEIVE *msg, uint8_t *count);

#endif



