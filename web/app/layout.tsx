import type { Metadata, Viewport } from "next";
import { Comfortaa, Unbounded } from "next/font/google";
import "./globals.css";

const display = Unbounded({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-display" });
const body = Comfortaa({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-body" });

export const metadata: Metadata = {
  title: "STRATA Brief",
  description: "Describe the house you want in plain words. STRATA turns it into a design brief, then a code compliant BIM model.",
};

export const viewport: Viewport = { themeColor: "#1d203e", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable}`}>
      <body>{children}</body>
    </html>
  );
}
