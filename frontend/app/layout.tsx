import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// UI refresh (18.10, docs/design/ui-refresh); local since task 6a (chat 20.10): the files sit in app/fonts
// beside their licences (OFL, from github.com/google/fonts), so neither the build nor the demo needs a network
const sans = localFont({
  variable: "--font-hanken",
  src: "./fonts/HankenGrotesk[wght].ttf",
  weight: "400 800",
});

const mono = localFont({
  variable: "--font-plex-mono",
  src: [
    { path: "./fonts/IBMPlexMono-Regular.ttf", weight: "400" },
    { path: "./fonts/IBMPlexMono-Medium.ttf", weight: "500" },
    { path: "./fonts/IBMPlexMono-SemiBold.ttf", weight: "600" },
  ],
});

export const metadata: Metadata = {
  title: "MedAgentPro",
  // generic here; the catalog's own description replaces it once the server answers (chat 19.10, C)
  description: "Query assistance over records (demo)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
