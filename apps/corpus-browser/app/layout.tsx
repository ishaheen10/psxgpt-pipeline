import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const fraunces = Fraunces({
  variable: "--font-editorial",
  subsets: ["latin"],
});

const plexSans = IBM_Plex_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") || (host.startsWith("localhost") ? "http" : "https");
  const origin = `${protocol}://${host}`;
  const title = "Ledger — Authoritative Corpus Browser";
  const description =
    "Search authoritative source text with BM25, inspect deterministic checks, and assemble Analyst Context by hand.";
  const imageUrl = `${origin}/og.png`;
  return {
    metadataBase: new URL(origin),
    title,
    description,
    openGraph: {
      type: "website",
      url: origin,
      siteName: "Ledger",
      title,
      description,
      images: [{ url: imageUrl, width: 1734, height: 907, alt: "Ledger — Search the evidence. Build the context." }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [imageUrl],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${fraunces.variable} ${plexSans.variable} ${plexMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
