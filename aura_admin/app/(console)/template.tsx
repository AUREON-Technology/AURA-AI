// Re-mounted on every console navigation (unlike layout.tsx), so each page eases in.
export default function ConsoleTemplate({ children }: { children: React.ReactNode }) {
  return <div className="motion-safe:animate-page-in">{children}</div>;
}
