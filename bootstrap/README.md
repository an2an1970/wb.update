# WB SD first-boot bootstrap (experimental)
Target: Raspberry Pi OS **Bookworm 64-bit Desktop**, Raspberry Pi 5.

1. Flash the Raspberry Pi OS image to SD.
2. Mount the FAT32 bootfs partition.
3. Copy a published WB .wbpkg to `bootfs/wb-install/package.wbpkg`.
4. Copy `bootstrap/wb-firstboot.sh` to `bootfs/wb-firstboot.sh`.
5. Append to the **single line** in `bootfs/cmdline.txt`:
   `systemd.run="/bin/bash /boot/firmware/wb-firstboot.sh" systemd.run_success_action=none systemd.run_failure_action=none systemd.unit=kernel-command-line.target`
6. Boot Pi. The OS firstboot init may reboot once; systemd then launches WB installer.
7. Inspect `/var/log/wb-firstboot.log` on failure.

The bootstrap removes its own kernel command line trigger only after the package has successfully staged its resume service and the QR UART overlay has been installed. The existing package currently performs OS provisioning; this is a transitional implementation, not yet separated. Requires network for apt. No hardware end-to-end test yet.
