#ifndef APP_RESET_H
#define APP_RESET_H

#include "int_bootloader.h"

typedef enum
{
    BOOTLOADER_STATE_DEFAULT,
    BOOTLOADER_STATE_INIT,
    BOOTLOADER_STATE_RUN,
    BOOTLOADER_STATE_REC_DATA,
    BOOTLOADER_STATE_CHECK_DATA,
    BOOTLOADER_STATE_JUMP_TO_APP,
} Bootloader_state;

extern Bootloader_state boot_state;

void App_bootloader_Init(void);
void App_bootloader_run(void);
void App_bootloader_rec_data(void);
bootloader_result_enum App_bootloader_check_data(void);
uint8_t App_bootloader_jump_to_app(void);
void App_bootloader_work(void);

#endif
