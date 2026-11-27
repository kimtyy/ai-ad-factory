import type { MenuItem } from '../src/types';

export type BusinessType = 'food' | 'service';
export function determineBusinessType(category?: string, storeName?: string): BusinessType;
export function buildHookCopy(storeName: string, menuName: string, category?: string, businessType?: BusinessType): string;
export function swapHookMenu(args: {
  hookCopy: string;
  storeName: string;
  fromMenu: string;
  toMenu: string;
  category: string;
  businessType: BusinessType;
}): string;
export function hasReviewText(s?: string): boolean;
export function serviceStrengths(storeName: string): { title: string; description: string; mentionRate: string; sampleReview: string }[];
export function serviceGroup(category?: string, storeName?: string): 'medical' | 'repair' | 'beauty' | 'education' | 'other';
export function serviceMenus(storeName: string, category?: string): MenuItem[];
