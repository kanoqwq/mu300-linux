# Hardware and modem fixes

- Implement new firmware-side control and background features in ucode. Python
  remains available for host builds, offline diagnostics and tests, rather than
  being the default runtime implementation. Prefer existing ubus, modem and
  device events plus deadline timers; do not introduce independent polling when
  the required event or accounting sample already exists. Preserve native
  hardware drivers and existing AT ownership/ordering during migrations.
- For runtime migrations, complete at least six documented regression rounds
  before reporting readiness. Compare against the original implementation and
  cover real RPC/event dispatch, concurrency/fault recovery, upgrade/rollback
  with preserved user data, and sustained execution. Fix differences and rerun
  the final matrix; keep offline results distinct from hardware acceptance.

- NR0 is the sole ingress for automatic modem URC events. Do not select an
  event channel by SIM number or wait on NR3 for SIM2 notifications. The
  ingress channel does not identify the event's SIM; confirm the target using
  the existing explicit SIM-selection/query mechanism. This rule concerns
  unsolicited reception only: preserve each control/data operation's existing
  AT owner, command channel, queue and ordering. Never move all AT/control
  traffic onto NR0 or add a competing reader to its existing owner.
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

# Rootfs and kernel package architecture

- These ports intentionally combine an OpenWrt armsr base rootfs/package ecosystem
  with a separately built, model-specific kernel and matching modules. The APK
  database's base-distribution kernel identity is not the running kernel identity;
  a difference from `uname -r`, a firmware version or an ABI hash alone is not a bug.
- Preserve the base rootfs's kernel/kmod package metadata and dependency strategy.
  Do not rewrite them into custom native-kernel identities, generate capability
  packages or add native-kernel world pins merely to make the records agree.
  Diagnose actual repository/package conflicts before changing package policy.
- Validate the delivered Image, modules, source/config receipts and model pairing
  through the existing build paths. Retaining armsr metadata does not establish
  compatibility of third-party `.ko` files and is not permission to force-load them
  or bypass existing module checks. Keep artifact-integrity checks separate from
  runtime feature gating; do not add fixed firmware/hash allowlists.
- For updates, preserve unrelated user packages, constraints and configuration.
  Cleanup of the withdrawn generated native-kernel metadata must remain scoped to
  confirmed records from that migration and use the normal OTA transaction/rollback.
  Do not infer an `apk upgrade` boot failure from metadata differences alone.
- Read `docs/NATIVE-KERNEL-APK.zh-CN.md` before changing kernel package metadata.

# Local builds and release packaging

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

# Shared installer ownership

- installer/ is the Git submodule https://github.com/kanoqwq/openwrt-tools, pinned to a reviewed commit. Read installer/AGENTS.md and installer/docs/BUILD.zh-CN.md before installer work.
- Commit shared UI/backend/Android/runtime changes in that repository, then update this parent repository's gitlink. Never restore a full generated installer mirror or auto-follow an unpinned branch during packaging.
- Fresh checkouts must run git submodule update --init --recursive. Parent CI must use recursive submodules.
- E5 and MU300 keep distinct NV, storage identity and boot adapters. Both support eMMC/UFS. A model-name mismatch is a typed yes decision, not a hardcoded allowlist; other integrity and partition checks remain in effect.
- Reuse one verified native installer build across both parents with the existing artifact environment variables; do not duplicate SDK, node_modules, Cargo targets or rootfs caches.
