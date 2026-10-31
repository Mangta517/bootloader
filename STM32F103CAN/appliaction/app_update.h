#ifndef APP_UPDATE_H
#define APP_UPDATE_H

typedef enum
{
    APP_UPDATE_WAIT = 0,  /* 等待更新命令。 */
    APP_UPDATE_SEND       /* 发送更新内容。 */
} app_update_state_enum;

/* 定义于 app_update.c，由初始化和命令校验更新，app_update_work 读取。 */
extern app_update_state_enum app_update_state;

void app_update_init(void);
void app_update_work(void);
void app_update_wait(void);
void app_update_send(void);

#endif
