# Hardware and modem fixes

- Use each model's stock Android firmware behavior as the reference for power,
  charging, battery accounting, radio initialization and reconnection.
- Prefer the actual device's driver/RIL binary and device tree over a merely
  similar vendor source tree. Record firmware versions and the evidence used.
- Separate confirmed defects, hypotheses and untested hardware behavior in
  reports. AOSP explains the framework design; its default timers are not proof
  of the model's carrier or vendor configuration.
- Do not hide a symptom with arbitrary percentage caps, forced recalibration,
  radio resets, disabled sleep or reduced functionality. Preserve existing AT
  ownership, command ordering, boot replay and explicit user choices.
- Apply shared fixes to shared build/update paths. Keep charger-chip and board
  differences in the model profiles, and do not call an offline test a successful
  hardware validation.

# Local builds and release packaging

- Read `docs/LOCAL-BUILD.zh-CN.md` before a local build or release. Use the
  version-controlled `tools/build-release.py` entry point for task logging,
  workspace locking and release assembly. Never generate another release worker
  in `work/`, or execute/modify an old worker to assemble a new release.
- Reuse the existing `/home/kano/openwrt-build` workspace on this machine.
  Register input paths in `tools/build-layout.json` or an explicitly selected
  local config. Do not migrate builds to C:, create timestamped source/object
  trees, clone another kernel, export another Docker volume, or duplicate a
  complete rootfs as a shortcut. Existing model-specific prepared source/object
  caches are retained; never share a patched source tree across models blindly.
- Keep final local releases under `release/`, task logs/status under
  `out/build-logs/` and `out/build-state/`, and package staging under the fixed
  workspace `package-work/mu300-release/<series>/`. Investigations belong under
  `work/` or `dumps/`; they are not production build inputs unless registered.
- Separate native Windows/macOS installer builds, kernel/APK compilation, and
  package assembly. Check source fingerprints and matching Image/modules before
  reuse. Do not rebuild unchanged inputs merely because packaging is requested,
  or bypass a stale-input rejection to avoid compiling a changed dependency.
- Use only the model's tracked build/prepare scripts. Do not use `git reset`,
  `git clean`, archive extraction over a prepared tree, or an arbitrary cache
  substitution to switch profiles. Stop and describe missing/invalid registered
  inputs instead of silently choosing another tree or an old package.
- Successful assembly removes only its owned staging, after archive verification.
  Failed staging is retained for diagnosis and requires `clean-staging` before
  reuse. Never automatically delete source/object/download caches, device dumps,
  installation backups, or previous releases. Do not run `docker system prune`
  or remove volumes as routine build cleanup.
