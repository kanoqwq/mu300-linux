import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
export interface Device {serial: string; model?: string; label?: string; state?: string; wireless?: boolean; system?: string; slot?: string; root?: boolean; readiness?: string; encrypted?: boolean; existing?: boolean; tf?: string; profile?: string; free_bytes?: number; userdata_bytes?: number}
export interface AdbConnection {endpoint: string; state?: string; devices: Device[]}
export interface Package {path: string; name: string; adapter: string; profile: string; kernel: string}
export interface ResetPrompt {confirmation_required: 'format_android'; reason: string; reset_token: string; model: string; serial_hint: string}
export interface Transaction {
  id: string; status: string; stage: string; device: Device; package: Package;
  choices: {method: string; host?: string}; plan: {wipe: boolean; data_action?: string; preservation_error?: string; layout_source?: 'original_record' | 'device'; released_unallocated_bytes?: number; two_stage: boolean; offline?: boolean; openwrt_bytes: number; android_bytes?: number; previous_android_bytes?: number; reclaimed_bytes?: number; target: string; grow?: boolean; experimental?: boolean; gap_start?: number};
  uninstall_armed?: boolean; completed: string[]; unsafe: boolean; updated: number; revision?: number;
  stage_started_at?: number; stage_finished_at?: number;
  details?: {host?: string; tool?: string; reboot_manually?: boolean; boot_confirmed?: boolean; verification_error?: string; connection_state?: string}; error?: {message: string; retryable: boolean; code: string};
  result?: {filesystem_bytes: number; url: string; backup_directory: string; temporary: boolean; uninstalled?: boolean; android_bytes?: number; reclaimed_bytes?: number; released_bytes?: number};
}
export interface Transfer {name: string; bytes: number; total: number; bytes_per_second: number}
export interface BackendEvent {type: string; data: unknown}
export const request = <T = unknown>(command: string, params: Record<string, unknown> = {}) => invoke<T>('backend_request', {command, params});
export const subscribe = (handler: (event: BackendEvent) => void) => listen<BackendEvent>('backend', event => handler(event.payload));
export const native = <T = string | null>(command: string) => invoke<T>(command);
export const gib = (bytes = 0) => (bytes / 1073741824).toFixed(2) + ' GiB';
export function stateGroup(stage: string): number {
  if (['plan', 'package', 'device', 'environment'].includes(stage)) return 0;
  if (['backup', 'prepare','uninstall_prepare','firmware_extract','firmware_convert'].includes(stage)) return 1;
  if (['uninstall_offline','android_boot', 'android_setup', 'usb_debugging', 'debug_authorization', 'magisk', 'root_permission', 'root_ready', 'recheck'].includes(stage)) return 2;
  if (['transfer_install', 'verify_capacity', 'verify_android', 'uninstall_restore_boot', 'openwrt_boot'].includes(stage)) return 3;
  return 4;
}
