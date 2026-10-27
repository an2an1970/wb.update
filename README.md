# wb.update — iteration 1: offline package builder

Produces a deterministic `.wbpkg` ZIP from **exact release artifacts**: Spring Boot fat JAR, UI `dist` ZIP, ESP32-S3 **v4 only** firmware/bootloader/partitions BINs. Requires Python 3.10+; no dependencies.

```bash
python3 packager/build.py build \
  --version 0.1.0 \
  --backend wb.srv.jar --ui ui.zip \
  --firmware wb-v4-esp32s3.bin \
  --bootloader wb-v4-esp32s3.bootloader.bin \
  --partitions wb-v4-esp32s3.partitions.bin \
  --backend-sha 822c2d0d8d783f0b576843632cd45737958f8305 \
  --ui-sha 7f09a972bf64bf29991d9b4c61f084f9b6f45918 \
  --firmware-sha dcaba88158e4d72a6d42858e5a200212e02a8336 \
  --output wb-0.1.0.wbpkg
python3 packager/build.py verify wb-0.1.0.wbpkg
```

Release assets for this build:
- wb.srv: `4.0--g822c2d0-pi` (fat JAR)
- wb.ui: `0.3-190` (ZIP)
- wb.arduino: `build-S3SIM_v1.9.158_dcaba88` (v4 files only)

**Security status:** SHA-256 protects against accidental corruption only; package signing/trusted provenance **not implemented yet**. Do not install packages on an instrument based solely on this verifier. Flash offsets are intentionally unspecified until verified from firmware build configuration. Installer, rollback, online downloader, and ESP flashing are not implemented in this iteration.
