import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并 class —— shadcn/ui 的标准做法。
 *
 * `clsx` 负责条件类，`twMerge` 负责**冲突消解**：
 * `cn('p-2', 'p-4')` → `'p-4'`（而不是两个都在，让 Tailwind 打架）。
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
