import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Project MIRROR",
  description: "An AI-native social simulation",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

