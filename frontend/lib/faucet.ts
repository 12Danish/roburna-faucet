export type Chain = {
  chain_id: number;
  name: string;
  currency_symbol: string;
  currency_decimals: number;
  payout_amount_wei: string;
  cooldown_seconds: number;
  explorer_url: string | null;
  faucet_address: string;
};

export type Challenge = {
  challenge_id: string;
  message: string;
  expires_at: string;
};

export type Claim = {
  claim_id: string;
  chain_id: number;
  status: "reserved" | "submitted" | "broadcast_unknown" | "confirmed" | "failed";
  amount_wei: string;
  transaction_hash: string | null;
  transaction_url: string | null;
  reserved_at: string;
  next_eligible_at: string | null;
  failure_code: string | null;
};

export function formatUnits(wei: string, decimals: number): string {
  const amount = BigInt(wei);
  const base = BigInt(10) ** BigInt(decimals);
  const whole = amount / base;
  const fraction = (amount % base).toString().padStart(decimals, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}

export async function faucetRequest<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/faucet${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data?.detail;
    const code = typeof detail === "object" ? detail?.code : detail;
    const error = new Error(typeof code === "string" ? code : "request_failed");
    Object.assign(error, { status: response.status, detail, retryAfter: response.headers.get("Retry-After") });
    throw error;
  }
  return data as T;
}
