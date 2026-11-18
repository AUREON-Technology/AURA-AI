import { forwardRef, type InputHTMLAttributes, type LabelHTMLAttributes, type SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const control = [
  "h-9 w-full min-w-0 rounded-sm border border-line-strong bg-bg px-3 text-sm text-fg",
  "placeholder:text-faint transition-[border-color,box-shadow] duration-150",
  "focus-visible:border-brand focus-visible:shadow-[0_0_0_3px_var(--brand-soft)] focus-visible:outline-none",
  "disabled:cursor-not-allowed disabled:opacity-55",
].join(" ");

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(control, className)} {...props} />;
});

/** A styled native select: keyboard, mobile pickers and form semantics come for free. */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={cn(control, "cursor-pointer pe-8", className)} {...props} />;
});

/** A label wrapping its control, so the control's accessible name is the label text. */
export function Field({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1.5 text-[12.5px] font-medium text-muted", className)} {...props} />
  );
}
