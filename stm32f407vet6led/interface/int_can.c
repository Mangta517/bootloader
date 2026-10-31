#include "int_can.h"
#include "can.h"

void int_can_init(void)
{
    /* 掩码为 0，接收所有 ID 的报文到 FIFO0。 */
    CAN_FilterTypeDef sFilterConfig = {0};
    sFilterConfig.FilterBank = 0;
    sFilterConfig.FilterMode = CAN_FILTERMODE_IDMASK;
    sFilterConfig.FilterScale = CAN_FILTERSCALE_32BIT;
    sFilterConfig.FilterIdHigh = 0x0000;
    sFilterConfig.FilterIdLow = 0x0000;
    sFilterConfig.FilterMaskIdHigh = 0x0000;
    sFilterConfig.FilterMaskIdLow = 0x0000;
    sFilterConfig.FilterFIFOAssignment = CAN_RX_FIFO0;
    sFilterConfig.FilterActivation = ENABLE;
    /* 过滤器 0~13 分配给 CAN1，14~27 分配给 CAN2。 */
    sFilterConfig.SlaveStartFilterBank = 14;
    if (HAL_CAN_ConfigFilter(&hcan1, &sFilterConfig) != HAL_OK)
    {
        Error_Handler();
    }

    if (HAL_CAN_Start(&hcan1) != HAL_OK)
    {
        Error_Handler();
    }
}

void int_can_send(uint16_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef TxHeader = {0};
    uint32_t TxMailbox;

    /* 等待发送邮箱空闲。 */
    while (HAL_CAN_GetTxMailboxesFreeLevel(&hcan1) == 0)
    {
    }

    /* 将需要发送的消息添加到发送邮箱。 */
    TxHeader.StdId = id;
    TxHeader.IDE = CAN_ID_STD;
    TxHeader.RTR = CAN_RTR_DATA;
    TxHeader.DLC = len;

    HAL_CAN_AddTxMessage(&hcan1, &TxHeader, data, &TxMailbox);
}

void int_can_receive_msg(CAN_MSG_RECEIVE *msg, uint8_t *count)
{
    *count = HAL_CAN_GetRxFifoFillLevel(&hcan1, CAN_RX_FIFO0);
    if (*count > 0)
    {
        /* 读取一条消息，帧头和数据直接存入接收结构体。 */
        if (HAL_CAN_GetRxMessage(&hcan1, CAN_RX_FIFO0, &msg->RxHeader, msg->data) != HAL_OK)
        {
            *count = 0;
        }
    }
}
