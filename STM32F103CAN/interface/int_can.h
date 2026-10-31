#ifndef INT_CAN_H
#define INT_CAN_H

#include "can.h"

/* MX_CAN_Init 后配置过滤器并启动 CAN，保持原有过滤与收发逻辑。 */
void can_init(void);
/* 标准数据帧，len 为 0~8；返回 0 表示提交邮箱成功，1 表示失败。 */
uint8_t can_send(uint16_t id, uint8_t *data, uint8_t len);
/* 轮询 FIFO0；返回 0 表示收到一帧，1 表示无报文或读取失败。 */
uint8_t can_receive(CAN_RxHeaderTypeDef *header, uint8_t *data);

#endif
