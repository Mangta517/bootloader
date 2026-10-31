#ifndef APP_CAN_H
#define APP_CAN_H

/* 主循环反复调用，每 1000 ms 以标准 ID 0 发送三字节 hyf 命令。 */
void app_can_process(void);

#endif
