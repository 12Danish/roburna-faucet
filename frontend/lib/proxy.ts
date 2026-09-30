import "server-only";

const backendUrl = process.env.FAUCET_API_URL ?? "http://127.0.0.1:8001";

export async function proxyFaucet(path: string, request?: Request): Promise<Response> {
  let url: URL;
  try {
    url = new URL(backendUrl);
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("invalid protocol");
  } catch {
    return Response.json({ detail: "Frontend API URL is invalid" }, { status: 500 });
  }

  try {
    const body = request?.method === "POST" ? await request.text() : undefined;
    if (body && body.length > 12_000) {
      return Response.json({ detail: "Request is too large" }, { status: 413 });
    }
    const upstream = await fetch(new URL(path, url), {
      method: request?.method ?? "GET",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store" });
    const retryAfter = upstream.headers.get("Retry-After");
    if (retryAfter) headers.set("Retry-After", retryAfter);
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Faucet API is unavailable" }, { status: 502 });
  }
}
