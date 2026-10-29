# Patched Flipper firmware

Official firmware 1.5.1 + `1.5.1-custom-adv-data.patch`.

Adds `furi_hal_bt_set_custom_adv_data(adv, len, scan_rsp, len)`: the app supplies the
raw advertising and scan response payloads, so the Flipper can mirror the Telly
remote's packets exactly (LE Limited Discoverable flags, appearance 0x0180,
manufacturer data in both packets). Stock 1.5.1 always advertises as General
Discoverable and only puts manufacturer data in the scan response.

Also adds `ble_svc_dev_info_set_pnp_id()` / `ble_svc_dev_info_clear_pnp_id()`: an optional
PnP ID characteristic in the Device Information service, so the Flipper can report the
remote's vendor/product ID and the TV loads Telly's key map. API 88.2 → 88.4.

```sh
git clone --branch 1.5.1 --recurse-submodules https://github.com/flipperdevices/flipperzero-firmware.git .fw151
cd .fw151 && git apply ../flipper/firmware/1.5.1-custom-adv-data.patch
mkdir -p applications_user/telly_remote
cp ../flipper/telly_remote/application.fam ../flipper/telly_remote/telly_remote.c applications_user/telly_remote/
./fbt COMPACT=1 DEBUG=0 updater_package fap_telly_remote
./fbt COMPACT=1 DEBUG=0 flash_usb_full   # stop server.py first; it shares the USB port
```

Revert: qFlipper → Update (official release). Don't downgrade below 1.1.
