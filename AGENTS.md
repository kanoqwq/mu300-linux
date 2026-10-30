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
