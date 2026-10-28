import type { Metadata } from "next";
import FertilizerReportClient from "./report-client";

export const dynamic = "force-static";

export const metadata: Metadata = {
  title: "Pakistan Fertilizer Analyst Workspace | Ledger",
  description: "Move from leading indicators to company impact, assemble bounded evidence, and keep a live sector thesis under review.",
  openGraph: {
    title: "Fertilizer Research Workspace",
    description: "From leading indicators to company impact, with a 100-page primary-source evidence bench.",
    images: [{ url: "/fertilizer-analyst-workspace-og.png", width: 1672, height: 941 }],
  },
};

export default function FertilizerReportPage() {
  return <FertilizerReportClient />;
}
