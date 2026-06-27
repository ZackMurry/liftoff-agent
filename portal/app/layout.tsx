import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Liftoff Portal",
  description: "Management portal for drone simulation CI runs."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
