"use client";

import type { ComponentPropsWithoutRef, HTMLAttributes } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogClose = DialogPrimitive.Close;

/**
 * Portaled to the end of <body>, so its input is the last one in DOM order. The backdrop fades and
 * the panel scales in; both run in reverse on close (Radix waits for the exit animation).
 */
export function DialogContent({
  className,
  children,
  hideClose = false,
  ...props
}: ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { hideClose?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-40 bg-overlay backdrop-blur-[3px]",
          "motion-safe:data-[state=open]:animate-fade-in motion-safe:data-[state=closed]:animate-fade-out",
        )}
      />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-[16vh] left-1/2 z-50 w-[min(460px,calc(100vw-32px))] -translate-x-1/2",
          "rounded-lg border border-line-strong bg-surface p-5 text-fg shadow-raised focus-visible:outline-none",
          "motion-safe:data-[state=open]:animate-dialog-in motion-safe:data-[state=closed]:animate-dialog-out",
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute top-3.5 end-3.5 grid size-7 place-items-center rounded-sm text-faint transition-colors hover:bg-surface-variant hover:text-fg"
          >
            <X aria-hidden="true" className="size-4" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogTitle({ className, ...props }: ComponentPropsWithoutRef<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn("pe-8 text-base leading-snug font-semibold", className)} {...props} />;
}

export function DialogDescription({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("mt-1 text-[13px] text-muted", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-5 flex flex-wrap justify-end gap-2", className)} {...props} />;
}
