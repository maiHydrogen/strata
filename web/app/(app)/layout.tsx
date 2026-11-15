import { BriefProvider } from "@/components/BriefStore";
import Shell from "@/components/Shell";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <BriefProvider>
      <Shell>{children}</Shell>
    </BriefProvider>
  );
}
