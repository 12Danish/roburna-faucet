import { proxyFaucet } from "@/lib/proxy";

export async function POST(request: Request) {
  return proxyFaucet("/claims", request);
}
