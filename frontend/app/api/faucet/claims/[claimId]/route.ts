import { proxyFaucet } from "@/lib/proxy";

export async function GET(_request: Request, context: RouteContext<"/api/faucet/claims/[claimId]">) {
  const { claimId } = await context.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(claimId)) {
    return Response.json({ detail: "Invalid claim ID" }, { status: 400 });
  }
  return proxyFaucet(`/claims/${claimId}`);
}
