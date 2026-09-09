import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ORCA · Agentic AI Marine Intelligence Platform",
  description:
    "SIH 2026 prototype (PS 26176). Multi-agent marine intelligence for Indian fishermen and coastal officers, with a live reasoning trace, live ocean and weather data, and a deterministic hazard rule engine.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#050b14",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
