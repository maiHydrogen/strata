"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import LayoutView from "@/components/LayoutView";
import { useBrief } from "@/components/BriefStore";
import { useDesign } from "@/components/DesignStore";

/** /layout needs a confirmed brief; without one it sends you back to /brief. */
export default function LayoutPage() {
  const router = useRouter();
  const { ready, done, brief, code, requireCode } = useBrief();
  const { approve } = useDesign();
  const ok = done && brief !== null;

  useEffect(() => {
    if (ready && !ok) router.replace("/brief");
  }, [ready, ok, router]);

  if (!ok) return null;
  return (
    <div className="content scroll">
      <LayoutView brief={brief} code={code}
                  onNeedCode={() => { requireCode(); router.push("/brief"); }}
                  onApproved={approve} />
    </div>
  );
}
