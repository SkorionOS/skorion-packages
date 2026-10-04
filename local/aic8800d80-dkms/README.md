# AIC8800 source tracking

This local recipe replaces the AUR recipe whose constant `1.0.0-6` version
hid changes to its floating Git source. `pkgver()` now records the actual
driver version, revision count and commit, so the existing update checker can
detect source updates without workflow changes.

The source remains upstream `main`, the same hardware profile selected by the
former default-branch recipe. Upstream now requires `legacy-mcu1` for devices
reporting `chip_mcu_id=1`; this package does not silently switch that profile or
mix firmware between branches. Validate the affected USB IDs before release.

The staged DKMS name, version and `/usr/src` directory must agree. The package
includes the upstream ZLP companion module, matching firmware variants, udev
rules and the Pandora USB mode-switch configuration. It never runs upstream
`install.sh`, builds kernel modules during packaging or modifies the host.
