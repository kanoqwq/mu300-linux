# Independent U30 Pro port

- This repository is D:/Git/u30pro_openwrt/mu300-linux, branch u30pro-bringup,
  based on kanoqwq/mu300-linux minikano-openwrt-feats at 29061dc8, never main.
- Read docs/U30PRO-PORT.zh-CN.md. U30 Pro uses UMS9632, Linux 6.12.94,
  AIC8800 SDIO and its own native panel. Never assemble MU300 hardware images
  as a U30 Pro release or run the inherited MU300 platform services blindly.
- devices/u30pro/native source provenance is in devices/u30pro/reference.json;
  local adaptations must carry their original and current hashes and rationale.
- Existing U30Pro, MU300 and E5 repositories and the user's clean reference
  checkout are read-only for this task. Do not change or push them.
- Device access is read-only until the user authorizes the particular write,
  reboot or hardware trial. Do not change IMEI/NV. No fastboot or SPD.
- Android/Linux slots and storage geometry must be verified per device.
  Never default Android to A, copy reference LBAs, use reserve2 config storage,
  or inherit the upstream whole-device/A-B installer.
- MU300/E5's maintained modem framework is authoritative: retain its AT
  ownership, SPACTCARD selection, transaction ordering, boot replay and event
  recovery. Hardware adapters may map native ports and load CP firmware; they
  must not remove protocol steps based solely on physical-channel grouping.
- On U30 Pro one MU300 bridge owns general/data ports nr2/nr0 for bank 0 and
  nr5/nr3 for bank 1. Physical bank identity alone does not select the firmware
  SIM context: preserve SPACTCARD. Never start the reference native atd/celld.
  CP loading, NV notification, display and hardware drivers come from U30 Pro;
  installation/update/storage remain MU300 mu300root, not reserve2/overlay.


# Hardware and modem fixes

- Before adapting UFS hardware, read `docs/UFS-PORTING.zh-CN.md`. It records
  the U30 Air investigation, 512/4K geometry contract, trial boot recovery,
  and the separate E5/Changxing 60 follow-up. Do not use fastboot.
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

# Backport architecture

- Fully replace a migrated operation or keep its original implementation until
  the rewrite is complete. Do not add availability/version probes that silently
  select an older backend, RPC or algorithm after the new one fails.
- Keep platform/model choices explicit. Validate required dependencies and
  report failures directly; retain safety checks and recovery within the chosen
  implementation.

# Local builds and release packaging

- A device hotfix is not complete until its production source, hardware profile,
  boot/service ordering, build/install path and relevant regression checks are
  updated in this repository. Never use files from work/, a deployed device or
  an old package as the production fix. Record temporary diagnostics separately.
- Before reporting a hotfix ready for release, compare the prepared root with
  the current sources and verify changed native binaries against their build
  receipts. Register new files/services/catalogs in the tracked assembly path;
  a successful copy to the device alone is not release integration.

- Read `docs/LOCAL-BUILD.zh-CN.md` and `docs/AGENT-BUILD.zh-CN.md` before a local build or release. Use the
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
  workspace `package-work/u30pro-release/`. Investigations belong under
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

# Shared installer ownership

- installer/ is the Git submodule https://github.com/kanoqwq/openwrt-tools, pinned to a reviewed commit. Read installer/AGENTS.md and installer/docs/BUILD.zh-CN.md before installer work.
- Commit shared UI/backend/Android/runtime changes in that repository, then update this parent repository's gitlink. Never restore a full generated installer mirror or auto-follow an unpinned branch during packaging.
- Fresh checkouts must run git submodule update --init --recursive. Parent CI must use recursive submodules.
- E5, MU300 and U30 Pro keep distinct NV, storage identity and boot adapters. Both support eMMC/UFS. A model-name mismatch is a typed yes decision, not a hardcoded allowlist; other integrity and partition checks remain in effect.
- Reuse one verified native installer build across both parents with the existing artifact environment variables; do not duplicate SDK, node_modules, Cargo targets or rootfs caches.
