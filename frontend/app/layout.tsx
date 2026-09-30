import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Roburna Testnet Faucet | Roburna Labs",
  description: "Request RBAT to build and test on Roburna Testnet.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="en"><body>{children}</body></html>;
}
