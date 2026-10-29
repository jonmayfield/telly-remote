// Telly Remote: pairs with the TV as a BLE HID remote and exposes a `telly`
// CLI command so a computer can press keys over USB.
//
//   telly k <hex> [ms]   tap a keyboard usage (optionally hold for ms)
//   telly c <hex> [ms]   tap a consumer-control usage
//   telly status         print "connected" / "advertising" / "off"
//   telly adv <hex>      replace the raw advertising payload
//   telly scan <hex>     replace the raw scan response payload
//   telly seq <hold> <k|c><hex>:<gap> ...  play a key sequence on-device
//   telly ir <proto> <addr> <cmd>   send an IR code (the built-in `ir tx`
//                        refuses to run while any app is open)
//   telly unpair         forget bonded devices (app-local keys only)
//
// Needs firmware 1.5.1 + flipper/firmware/1.5.1-custom-adv-data.patch
// (furi_hal_bt_set_custom_adv_data).

#include <furi.h>
#include <furi_hal_bt.h>
#include <gui/gui.h>
#include <bt/bt_service/bt.h>
#include <cli/cli.h>
#include <extra_profiles/hid_profile.h>
#include <infrared.h>
#include <infrared/worker/infrared_transmit.h>
#include <stdlib.h>

#define TAG          "TellyRemote"
#define KEYS_PATH    APP_DATA_PATH(".bt_hid.keys")
#define DEFAULT_HOLD 60

// Exact payloads captured from the real TYRC-1 remote in pairing mode
// (raw HCI capture with PacketLogger):
//   flags 0x05 (LE Limited Discoverable), HID service, name "BT38B",
//   manufacturer 0x005D 55 66; scan response adds appearance 0x0180
//   (remote control) and manufacturer 0x005D 55 88.
static uint8_t adv_data[31] = {
    0x02, 0x01, 0x05,
    0x03, 0x03, 0x12, 0x18,
    0x06, 0x09, 'B', 'T', '3', '8', 'B',
    0x05, 0xFF, 0x5D, 0x00, 0x55, 0x66,
};
static uint8_t adv_len = 20;
static uint8_t scan_rsp[31] = {
    0x03, 0x03, 0x12, 0x18,
    0x03, 0x19, 0x80, 0x01,
    0x06, 0x09, 'B', 'T', '3', '8', 'B',
    0x05, 0xFF, 0x5D, 0x00, 0x55, 0x88,
};
static uint8_t scan_len = 21;

typedef struct {
    Bt* bt;
    CliRegistry* cli;
    Gui* gui;
    ViewPort* view_port;
    FuriMessageQueue* input_queue;
    FuriHalBleProfileBase* profile;
    FuriMutex* mutex;
    BtStatus status;
    uint32_t presses;
    char last[24];
} TellyRemote;

static const BleProfileHidParams hid_params = {
    .device_name_prefix = "BT38B",
    .mac_xor = 0,
};

static void telly_get_gap_config(GapConfig* config, FuriHalBleProfileParams params) {
    ble_profile_hid->get_gap_config(config, params);
    snprintf(config->adv_name, sizeof(config->adv_name), "%cBT38B", 0x09);
    config->appearance_char = 0x0180; // Generic remote control
    config->pairing_method = GapPairingNone;
    // Telink OUI like the real remote, unique low bytes. LSB first.
    const uint8_t mac[GAP_MAC_ADDR_SIZE] = {0x01, 0x11, 0x7E, 0x38, 0xC1, 0xA4};
    memcpy(config->mac_address, mac, sizeof(mac));
}

static FuriHalBleProfileTemplate telly_profile;

static const char* status_text(BtStatus s) {
    switch(s) {
    case BtStatusConnected:
        return "connected";
    case BtStatusAdvertising:
        return "advertising";
    default:
        return "off";
    }
}

static void draw_callback(Canvas* canvas, void* ctx) {
    TellyRemote* app = ctx;
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "Telly Remote");
    canvas_set_font(canvas, FontSecondary);
    char line[40];
    snprintf(line, sizeof(line), "BLE: %s", status_text(app->status));
    canvas_draw_str(canvas, 2, 28, line);
    snprintf(line, sizeof(line), "Presses: %lu", app->presses);
    canvas_draw_str(canvas, 2, 40, line);
    snprintf(line, sizeof(line), "Last: %s", app->last);
    canvas_draw_str(canvas, 2, 52, line);
    canvas_draw_str(canvas, 2, 63, "Hold Back to exit");
    furi_mutex_release(app->mutex);
}

static void input_callback(InputEvent* event, void* ctx) {
    TellyRemote* app = ctx;
    furi_message_queue_put(app->input_queue, event, 0);
}

static void bt_status_callback(BtStatus status, void* ctx) {
    TellyRemote* app = ctx;
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    app->status = status;
    furi_mutex_release(app->mutex);
    view_port_update(app->view_port);
}

static void apply_adv(void) {
    furi_hal_bt_set_custom_adv_data(adv_data, adv_len, scan_rsp, scan_len);
    furi_hal_bt_stop_advertising();
    furi_hal_bt_start_advertising();
}

static uint8_t parse_hex(const char* h, uint8_t* out, uint8_t max) {
    uint8_t len = 0;
    unsigned byte;
    while(*h && len < max) {
        while(*h == ' ') h++;
        if(!*h || sscanf(h, "%2x", &byte) != 1) break;
        out[len++] = (uint8_t)byte;
        h += 2;
    }
    return len;
}

static bool press(TellyRemote* app, char kind, uint16_t code, uint32_t hold_ms) {
    // In firmware 1.5.1 these return true on *failure*: they pass through
    // ble_gatt_characteristic_update(), which returns result != BLE_STATUS_SUCCESS.
    bool failed;
    if(kind == 'k') {
        failed = ble_profile_hid_kb_press(app->profile, code);
        furi_delay_ms(hold_ms);
        failed |= ble_profile_hid_kb_release(app->profile, code);
    } else {
        failed = ble_profile_hid_consumer_key_press(app->profile, code);
        furi_delay_ms(hold_ms);
        failed |= ble_profile_hid_consumer_key_release(app->profile, code);
    }
    bool ok = !failed;
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    app->presses++;
    snprintf(app->last, sizeof(app->last), "%c %04X%s", kind, code, ok ? "" : " ERR");
    furi_mutex_release(app->mutex);
    view_port_update(app->view_port);
    return ok;
}

static void cli_callback(PipeSide* pipe, FuriString* args, void* ctx) {
    UNUSED(pipe);
    TellyRemote* app = ctx;
    const char* a = furi_string_get_cstr(args);

    if(strncmp(a, "status", 6) == 0) {
        printf("%s\r\n", status_text(app->status));
        return;
    }
    if(strncmp(a, "adv", 3) == 0) {
        adv_len = parse_hex(a + 3, adv_data, sizeof(adv_data));
        apply_adv();
        printf("adv %u bytes\r\n", adv_len);
        return;
    }
    if(strncmp(a, "scan", 4) == 0) {
        scan_len = parse_hex(a + 4, scan_rsp, sizeof(scan_rsp));
        apply_adv();
        printf("scan %u bytes\r\n", scan_len);
        return;
    }
    if(strncmp(a, "seq ", 4) == 0) {
        // telly seq <hold_ms> <kind><hex>:<gap_ms> ...  e.g. "seq 10 c40:120 k52:35"
        // Plays a whole key sequence on-device, without a USB round trip per key.
        const char* s = a + 4;
        unsigned long hold;
        int used = 0;
        if(sscanf(s, "%lu%n", &hold, &used) != 1 || hold > 1000) {
            printf("usage: telly seq <hold_ms> <k|c><hex>:<gap_ms> ...\r\n");
            return;
        }
        s += used;
        if(app->status != BtStatusConnected) {
            printf("err not connected (%s)\r\n", status_text(app->status));
            return;
        }
        unsigned steps = 0;
        bool ok = true;
        while(*s && steps < 200) {
            while(*s == ' ') s++;
            if(!*s) break;
            char kind;
            unsigned code;
            unsigned long gap;
            if(sscanf(s, "%c%x:%lu%n", &kind, &code, &gap, &used) != 3 ||
               (kind != 'k' && kind != 'c') || code > 0xFFFF || gap > 5000) {
                printf("err bad step %u\r\n", steps);
                return;
            }
            ok &= press(app, kind, (uint16_t)code, hold);
            furi_delay_ms(gap);
            s += used;
            steps++;
        }
        printf(ok ? "ok %u\r\n" : "err send failed after %u\r\n", steps);
        return;
    }
    if(strncmp(a, "ir ", 3) == 0) {
        char proto[16];
        unsigned long address, command;
        if(sscanf(a + 3, "%15s %lx %lx", proto, &address, &command) != 3) {
            printf("usage: telly ir <protocol> <address hex> <command hex>\r\n");
            return;
        }
        InfraredMessage msg = {
            .protocol = infrared_get_protocol_by_name(proto),
            .address = address,
            .command = command,
            .repeat = false,
        };
        if(msg.protocol == InfraredProtocolUnknown) {
            printf("err unknown protocol\r\n");
            return;
        }
        infrared_send(&msg, 1);
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        app->presses++;
        snprintf(app->last, sizeof(app->last), "IR %04lX", command);
        furi_mutex_release(app->mutex);
        view_port_update(app->view_port);
        printf("ok\r\n");
        return;
    }
    if(strncmp(a, "unpair", 6) == 0) {
        bt_forget_bonded_devices(app->bt);
        printf("ok\r\n");
        return;
    }

    char kind = 0;
    unsigned code = 0;
    unsigned long hold = DEFAULT_HOLD;
    int n = sscanf(a, "%c %x %lu", &kind, &code, &hold);
    if(n < 2 || (kind != 'k' && kind != 'c') || code > 0xFFFF || hold > 5000) {
        printf("usage: telly k|c <hex> [ms] | status | adv <hex> | scan <hex> | unpair\r\n");
        return;
    }
    if(app->status != BtStatusConnected) {
        printf("err not connected (%s)\r\n", status_text(app->status));
        return;
    }
    printf(press(app, kind, (uint16_t)code, hold) ? "ok\r\n" : "err send failed\r\n");
}

int32_t telly_remote_app(void* p) {
    UNUSED(p);
    TellyRemote* app = malloc(sizeof(TellyRemote));
    memset(app, 0, sizeof(TellyRemote));
    strcpy(app->last, "-");
    app->mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    app->input_queue = furi_message_queue_alloc(8, sizeof(InputEvent));

    app->view_port = view_port_alloc();
    view_port_draw_callback_set(app->view_port, draw_callback, app);
    view_port_input_callback_set(app->view_port, input_callback, app);
    app->gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app->gui, app->view_port, GuiLayerFullscreen);

    // Switch the radio to the HID profile with app-local bonding keys so the
    // Flipper's normal phone-app pairing is left untouched.
    app->bt = furi_record_open(RECORD_BT);
    bt_disconnect(app->bt);
    furi_delay_ms(200);
    bt_keys_storage_set_storage_path(app->bt, KEYS_PATH);
    furi_hal_bt_set_custom_adv_data(adv_data, adv_len, scan_rsp, scan_len);
    // Register before advertising starts so the first status change isn't missed.
    bt_set_status_changed_callback(app->bt, bt_status_callback, app);
    telly_profile = *ble_profile_hid;
    telly_profile.get_gap_config = telly_get_gap_config;
    app->profile = bt_profile_start(app->bt, &telly_profile, (void*)&hid_params);
    furi_check(app->profile);
    furi_hal_bt_start_advertising();
    app->status = furi_hal_bt_is_active() ? BtStatusAdvertising : app->status;

    app->cli = furi_record_open(RECORD_CLI);
    cli_registry_add_command(app->cli, "telly", CliCommandFlagParallelSafe, cli_callback, app);

    InputEvent event;
    while(true) {
        if(furi_message_queue_get(app->input_queue, &event, FuriWaitForever) != FuriStatusOk)
            continue;
        if(event.key == InputKeyBack && event.type == InputTypeLong) break;
    }

    cli_registry_delete_command(app->cli, "telly");
    furi_record_close(RECORD_CLI);

    furi_hal_bt_set_custom_adv_data(NULL, 0, NULL, 0);
    bt_set_status_changed_callback(app->bt, NULL, NULL);
    bt_disconnect(app->bt);
    furi_delay_ms(200);
    bt_keys_storage_set_default_path(app->bt);
    furi_check(bt_profile_restore_default(app->bt));
    furi_record_close(RECORD_BT);

    gui_remove_view_port(app->gui, app->view_port);
    furi_record_close(RECORD_GUI);
    view_port_free(app->view_port);
    furi_message_queue_free(app->input_queue);
    furi_mutex_free(app->mutex);
    free(app);
    return 0;
}
