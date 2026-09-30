import { proxyFaucet } from "@/lib/proxy";

export async function POST(request: Request) {
  return proxyFaucet("/auth/challenge", request);
}
