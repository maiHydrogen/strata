import { BriefProvider } from "@/components/BriefStore";
import Shell from "@/components/Shell";
import { DesignProvider } from "@/components/DesignStore";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <BriefProvider>
      <DesignProvider>
        <Shell>{children}</Shell>
      </DesignProvider>
    </BriefProvider>
  );
}
