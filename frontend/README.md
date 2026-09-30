# Roburna faucet frontend

A one-page Next.js App Router frontend for the Roburna native-token faucet. It reads enabled chains from FastAPI, asks an injected EVM wallet (such as MetaMask) to sign a one-time SIWE message, submits the signed claim, and polls for confirmation.

## Run locally

Start the existing FastAPI Compose stack from `backend/`. Its API should be healthy at `http://127.0.0.1:8001/health`, and the worker should be running. The backend's SIWE domain and URI must match the URL opened in your browser. The current local Compose defaults are `localhost:3000` and `http://localhost:3000`.

Then run:

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

Open **http://localhost:3000** (use `localhost`, not `127.0.0.1`, for the SIWE origin). Connect MetaMask or another injected EVM wallet and press **Request test tokens**. The wallet may prompt to add or switch to Roburna Testnet (chain ID 159). Sign the message; this signature costs no gas. The page then displays the worker's claim status and transaction hash.

`FAUCET_API_URL` is read only by Next.js route handlers and defaults to `http://127.0.0.1:8001`. Set it to a reachable backend URL if Next.js runs in another container or server. `NEXT_PUBLIC_ROBURNA_RPC_URL` is the public RPC URL used only when the wallet needs to add Roburna Testnet. It is safe for browser exposure; do not put API credentials in it.

## Structure

- `app/page.tsx`: one-page shell and brand layout.
- `components/faucet-panel.tsx`: wallet, SIWE, claim, and polling UI.
- `lib/faucet.ts`: API types, request helper, and amount formatting.
- `app/api/faucet/*`: fixed same-origin route handlers forwarding public faucet calls.
- `lib/proxy.ts`: backend URL and response forwarding.

The browser never receives the distributor key. Claims are signed by the worker, not by the user's wallet. The browser signs only the SIWE ownership message.

## Deployment note

The Next.js proxy currently connects to FastAPI as one peer. FastAPI's IP/subnet limits therefore apply to that peer unless the deployment has a trusted reverse-proxy forwarding setup. Before exposing this to public traffic, configure the actual proxy chain and `BACKEND_TRUSTED_PROXY_CIDRS` so FastAPI resolves real client IPs; never trust arbitrary browser-supplied `X-Forwarded-For` headers. Wallet limits still apply independently.
