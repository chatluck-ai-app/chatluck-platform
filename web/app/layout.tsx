import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Chat Luck AI",
  description: "مساعد ذكي متعدد النماذج",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ar" dir="rtl">
      <body>{children}</body>
    </html>
  );
}
