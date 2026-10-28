import type { Metadata } from "next";
import SourceGraphClient from "./source-graph-client";

export const dynamic = "force-static";

export const metadata: Metadata = {
  title: "Source Graph | Ledger",
  description: "Explore how Pakistan's authoritative publishers, publication families and industry drivers connect.",
};

export default function SourceGraphPage() {
  return <SourceGraphClient />;
}

