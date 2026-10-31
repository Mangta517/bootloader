"""Compile the real update application and check the hyf state transition."""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
mock_header = r'''
#ifndef UPDATE_TEST_HAL_H
#define UPDATE_TEST_HAL_H
#include <stdint.h>
typedef struct
{
    uint32_t StdId, ExtId, IDE, RTR, DLC;
} CAN_RxHeaderTypeDef;
#define CAN_ID_STD 0
#define CAN_ID_EXT 4
#define CAN_RTR_DATA 0
#define CAN_RTR_REMOTE 2
#endif
'''
harness = r'''
#include "app_update.h"
#include "app_bootloader.h"
#include "int_can.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static CAN_RxHeaderTypeDef incoming = {0};
static uint8_t payload[8];
static uint8_t receive_result = 1;
static uint32_t init_count;
static uint32_t bootloader_calls;
app_flash_state_enum app_flash_state = APP_FLASH_WAIT;
static char output[2048];
static size_t output_length;

void can_init(void)
{
    assert(app_flash_state == APP_FLASH_DONE);
    init_count++;
}

void App_bootloader_init(void)
{
    app_flash_state = APP_FLASH_WAIT;
    bootloader_calls = 0;
}

void App_bootloader_update(void)
{
    assert(init_count == 0);
    bootloader_calls++;
    if (bootloader_calls < 4)
    {
        app_flash_state = APP_FLASH_RECEIVING;
    }
    else
    {
        app_flash_state = APP_FLASH_DONE;
    }
}

uint8_t can_receive(CAN_RxHeaderTypeDef *header, uint8_t *data)
{
    if (receive_result != 0)
    {
        return 1;
    }
    *header = incoming;
    memcpy(data, payload, sizeof(payload));
    receive_result = 1;
    return 0;
}

int test_printf(const char *format, ...)
{
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(output + output_length, sizeof(output) - output_length, format, arguments);
    va_end(arguments);
    assert(length >= 0 && (size_t)length < sizeof(output) - output_length);
    output_length += (size_t)length;
    return length;
}

static void run_startup(void)
{
STARTUP_CODE
}

int main(void)
{
    const char *invalid_commands[] = {"", "h", "hy", "HYF", "abc", "hyfX"};
    size_t index;
    size_t previous_length;

    run_startup();
    assert(bootloader_calls == 4 && app_flash_state == APP_FLASH_DONE);
    assert(init_count == 1 && app_update_state == APP_UPDATE_WAIT);
    assert(strstr(output, "CAN init OK") != NULL);
    assert(strstr(output, "Waiting update command: hyf") != NULL);
    previous_length = output_length;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT && output_length == previous_length);

    for (index = 0; index < sizeof(invalid_commands) / sizeof(invalid_commands[0]); index++)
    {
        memset(payload, 0, sizeof(payload));
        incoming.DLC = (uint32_t)strlen(invalid_commands[index]);
        memcpy(payload, invalid_commands[index], incoming.DLC);
        receive_result = 0;
        app_update_work();
        assert(app_update_state == APP_UPDATE_WAIT);
    }

    memcpy(payload, "hyf", 3);
    incoming.DLC = 8;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.DLC = 3;
    incoming.StdId = 1;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.StdId = 0;
    incoming.IDE = CAN_ID_EXT;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);
    incoming.IDE = CAN_ID_STD;
    incoming.RTR = CAN_RTR_REMOTE;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_WAIT);

    incoming.RTR = CAN_RTR_DATA;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_SEND);
    assert(strstr(output, "hyf OK, enter send update") != NULL);
    previous_length = output_length;
    receive_result = 0;
    app_update_work();
    assert(app_update_state == APP_UPDATE_SEND && output_length == previous_length && receive_result == 0);
    app_update_init();
    assert(init_count == 2 && app_update_state == APP_UPDATE_WAIT);
    puts("PASS: main loops until receive verification completes before CAN init, strict hyf command and WAIT -> SEND");
    return 0;
}
'''

main_source = (root / "Core/Src/main.c").read_text(encoding="utf-8")
startup_code = main_source.split("/* USER CODE BEGIN 2 */", 1)[1].split("/* USER CODE END 2 */", 1)[0]
harness = harness.replace("STARTUP_CODE", startup_code)

compiler = shutil.which("gcc") or r"C:\msys64\mingw64\bin\gcc.exe"
with tempfile.TemporaryDirectory(prefix="f103-app-update-") as folder:
    temporary = Path(folder)
    (temporary / "can.h").write_text(mock_header, encoding="utf-8")
    (temporary / "test.c").write_text(harness, encoding="utf-8")
    executable = temporary / "test_app_update.exe"
    subprocess.run([
        compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-Dprintf=test_printf",
        f"-I{temporary}", f"-I{root / 'appliaction'}", f"-I{root / 'interface'}",
        str(root / "appliaction/app_update.c"), str(temporary / "test.c"),
        "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)
