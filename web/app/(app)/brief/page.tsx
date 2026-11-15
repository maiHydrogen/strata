import type { Metadata } from "next";
import BriefChat from "@/components/BriefChat";

export const metadata: Metadata = { title: "Brief · STRATA" };

export default function Page() {
  return <BriefChat />;
}
