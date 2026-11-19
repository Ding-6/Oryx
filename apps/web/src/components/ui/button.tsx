import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * 按钮 —— shadcn/ui 的源码级组件（不是 npm 包）。
 *
 * 「源码级」的意思：组件代码**在我们仓库里**，改样式直接改这个文件。
 * 好处是能完全跟着 Oryx 的设计 token 走 ——
 * 如果用 npm 版的组件库，配色就成了它的品牌色，改不动。
 *
 * 变体对应现有前端的视觉语言：
 *   default  主色实心（--accent）
 *   outline  描边（--line-strong）
 *   ghost    无边框（次级操作）
 *   danger   危险（--danger）
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-[var(--r)] font-medium ' +
    'transition-colors disabled:pointer-events-none disabled:opacity-50 ' +
    'whitespace-nowrap select-none cursor-pointer',
  {
    variants: {
      variant: {
        default: 'bg-accent text-on-accent hover:opacity-90',
        outline: 'border border-line-strong bg-card text-ink hover:bg-panel',
        ghost: 'text-ink-2 hover:bg-panel',
        danger: 'bg-danger text-white hover:opacity-90',
      },
      size: {
        sm: 'h-8 px-3 text-[13px]',
        md: 'h-9 px-4 text-[13px]',
        lg: 'h-10 px-5 text-sm',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
    )
  },
)
Button.displayName = 'Button'

export { buttonVariants }
