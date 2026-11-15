import type { Metadata } from "next";
import LayoutPage from "@/components/LayoutPage";

export const metadata: Metadata = { title: "Layout · STRATA" };

export default function Page() {
  return <LayoutPage />;
}
