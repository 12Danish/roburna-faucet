import { proxyFaucet } from "@/lib/proxy";

export async function GET() {
  return proxyFaucet("/chains");
}
