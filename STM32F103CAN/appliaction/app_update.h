#ifndef APP_UPDATE_H
#define APP_UPDATE_H

/* 当前 CAN 联调使用 Flash 中已装载的 4480 字节 BIN，换文件时同步修改长度。 */
#define APP_UPDATE_STORED_SIZE_BYTES 4480
#define APP_UPDATE_DATA_CAN_ID 1
/* BIN 相邻数据帧间隔 10 ms，最后一帧成功后等待 1100 ms 再发送 CRC。 */
#define APP_UPDATE_FRAME_DELAY_MS 10
#define APP_UPDATE_CRC_DELAY_MS 1100

typedef enum
{
    APP_UPDATE_WAIT = 0,  /* 等待更新命令。 */
    APP_UPDATE_SEND,      /* 从 Flash 分帧发送更新内容。 */
    APP_UPDATE_DONE,      /* 本轮发送完成，避免周期 hyf 重复发送。 */
    APP_UPDATE_ERROR      /* 发送失败后停止，重新启动后重试。 */
} app_update_state_enum;

/* 定义于 app_update.c，由主循环中的更新流程读写。 */
extern app_update_state_enum app_update_state;

void app_update_init(void);
void app_update_work(void);
void app_update_wait(void);
void app_update_send(void);

#endif
