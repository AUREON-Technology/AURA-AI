"use client";

import { createContext, useContext } from "react";

import type { StepUp } from "@/lib/control";

/** The tab's step-up holder, shared by every Settings page (src/lib/control.ts). */
export const StepUpContext = createContext<StepUp | null>(null);

export function useStepUp(): StepUp {
  const stepUp = useContext(StepUpContext);
  if (!stepUp) {
    throw new Error("useStepUp outside the Settings layout");
  }
  return stepUp;
}
