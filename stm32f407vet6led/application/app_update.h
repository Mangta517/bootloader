#ifndef APP_UPDATE_H
#define APP_UPDATE_H

#include <stdint.h>

typedef enum
{
    APP_UPDATE_IDLE = 0,       /* 空闲，闪灯并等待串口 cmd 命令。 */
    APP_UPDATE_SEND_COMMAND,   /* 发送一次 CAN 更新命令。 */
    APP_UPDATE_RECEIVE,        /* 累计 ID 1 数据帧字节数，空闲 1 秒后打印并回到空闲状态。 */
    APP_UPDATE_VERIFY,         /* 预留固件校验。 */
    APP_UPDATE_DONE            /* 预留更新完成处理。 */
} app_update_state_enum;

/* 串口 cmd 命令切换发送状态，主循环执行状态机。 */
extern volatile app_update_state_enum app_update_state;
/* 主循环累计本轮实际收到的有效数据字节数，下次发送 hyf 前清零。 */
extern volatile uint32_t app_update_size_bytes;

/* USART1 硬件初始化后调用，启用空闲中断接收整条命令。 */
void app_update_init(void);
void app_update_work(void);

#endif
