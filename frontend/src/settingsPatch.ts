import type { RuntimeSettings } from './types';

export function changedRuntime(original: RuntimeSettings, edited: Partial<RuntimeSettings>): Partial<RuntimeSettings> {
  return Object.fromEntries(Object.entries(edited).filter(([key,value]) => JSON.stringify(original[key as keyof RuntimeSettings]) !== JSON.stringify(value))) as Partial<RuntimeSettings>;
}

export function disjointRuntimeChange(original: RuntimeSettings, latest: RuntimeSettings, patch: Partial<RuntimeSettings>): boolean {
  return Object.keys(patch).every(key => JSON.stringify(original[key as keyof RuntimeSettings]) === JSON.stringify(latest[key as keyof RuntimeSettings]));
}

export function clearSubmittedDraft(current: string, submitted: string): string {
  return current === submitted ? '' : current;
}
