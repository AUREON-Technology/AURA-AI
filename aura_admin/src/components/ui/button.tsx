import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  [
    "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-sm border font-medium",
    "select-none transition-[background-color,border-color,color,box-shadow,filter,transform] duration-150",
    "motion-safe:active:scale-[0.97] disabled:pointer-events-none disabled:opacity-55",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "border-line-strong bg-surface-variant text-fg hover:bg-hover",
        primary:
          "border-transparent bg-linear-135 from-brand to-brand-2 text-white shadow-glow hover:brightness-110",
        danger:
          "border-bad/45 bg-bad-soft text-bad hover:bg-bad/20",
        ghost: "border-transparent bg-transparent text-muted hover:bg-surface-variant hover:text-fg",
        outline: "border-line bg-surface text-muted hover:border-line-strong hover:text-fg",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-9 px-3.5 text-[13px]",
        lg: "h-10 px-4 text-sm",
        icon: "size-8 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});
