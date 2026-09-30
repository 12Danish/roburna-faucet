"use client";

import { useCallback, useEffect, useState } from "react";
import type { Chain, Challenge, Claim } from "@/lib/faucet";
import { faucetRequest, formatUnits } from "@/lib/faucet";

type EthereumProvider = {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on?(event: string, listener: (...args: unknown[]) => void): void;
  removeListener?(event: string, listener: (...args: unknown[]) => void): void;
};

declare global {
  interface Window { ethereum?: EthereumProvider }
}

function walletError(error: unknown): string {
  const value = error as { code?: number | string; message?: string; status?: number; detail?: { nextEligibleAt?: string }; retryAfter?: string };
  if (value?.code === 4001) return "You cancelled the wallet request.";
  if (value?.code === -32002) return "A wallet prompt is already open. Finish or reject it before trying again.";
  if (value?.code === -32601) return "This wallet cannot open an account chooser. Select an account in the wallet extension instead.";
  if (value?.message === "wallet_cooldown_active") {
    const date = value.detail?.nextEligibleAt;
    return date ? "This wallet can claim again after " + new Date(date).toLocaleString() + "." : "This wallet is still in its cooldown period.";
  }
  if (value?.message === "claim_already_pending") return "This wallet already has a claim in progress.";
  if (value?.message === "rate_limit_exceeded" || value?.status === 429) return "Too many requests. Please try again" + (value.retryAfter ? " in " + value.retryAfter + " seconds." : " later.");
  if (value?.message === "contract_recipient") return "Only regular wallet addresses can receive faucet funds.";
  if (value?.message === "recipient_balance_limit_exceeded") return "This payout would put your wallet above the faucet balance limit.";
  if (value?.message === "faucet_paused") return "The faucet is paused. Please try again later.";
  if (value?.message === "chain_unavailable") return "The Roburna chain check failed. Please try again shortly.";
  if (value?.status === 502 || value?.status === 503) return "The faucet is temporarily unavailable. Please try again later.";
  if (value?.message === "challenge_unavailable") return "The sign-in message expired. Please try again.";
  if (value?.message === "invalid_wallet_signature") return "The wallet signature could not be verified.";
  return value?.message && value.message !== "request_failed" ? value.message : "Something went wrong. Please try again.";
}

function claimMessage(claim: Claim): string {
  switch (claim.status) {
    case "reserved": return "Processing your request. The faucet is preparing your payout.";
    case "submitted": return "Transaction submitted. Waiting for confirmation.";
    case "broadcast_unknown": return claim.failure_code === "rpc_transaction_pool_unavailable"
      ? "Processing is paused: the Roburna RPC is not accepting transactions. Your claim is saved; no new request is needed."
      : "Checking whether the transaction reached the network. Please keep this claim ID.";
    case "confirmed": return "Your testnet tokens arrived in your wallet.";
    case "failed": return "This payout failed" + (claim.failure_code ? " (" + claim.failure_code + ")" : "") + ". You can try again.";
  }
}

function messageToHex(message: string): string {
  return "0x" + Array.from(new TextEncoder().encode(message), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function claimStorageKey(chainId: number, wallet: string): string {
  return "roburna-faucet-claim:" + chainId + ":" + wallet.toLowerCase();
}

export default function FaucetPanel() {
  const [chains, setChains] = useState<Chain[]>([]);
  const [wallet, setWallet] = useState("");
  const [claim, setClaim] = useState<Claim | null>(null);
  const [busy, setBusy] = useState(false);
  const [changingWallet, setChangingWallet] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(0);

  useEffect(() => {
    const initial = window.setTimeout(() => setNow(Date.now()), 0);
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, []);

  const chain = chains.find((item) => item.chain_id === 159) ?? chains[0];

  useEffect(() => {
    faucetRequest<{ chains: Chain[] }>("/chains")
      .then((data) => setChains(data.chains))
      .catch((reason) => setError(walletError(reason)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const provider = window.ethereum;
    if (!provider) return;
    const changed = (...args: unknown[]) => {
      const accounts = args[0];
      setWallet(Array.isArray(accounts) && typeof accounts[0] === "string" ? accounts[0] : "");
      setClaim(null);
      setError("");
    };
    provider.on?.("accountsChanged", changed);
    provider.request({ method: "eth_accounts" }).then((accounts) => changed(accounts)).catch(() => { });
    return () => provider.removeListener?.("accountsChanged", changed);
  }, []);

  useEffect(() => {
    if (!wallet || !chain) return;
    const saved = window.localStorage.getItem(claimStorageKey(chain.chain_id, wallet));
    if (!saved) return;
    let active = true;
    faucetRequest<Claim>("/claims/" + encodeURIComponent(saved))
      .then((value) => { if (active) setClaim(value); })
      .catch(() => { window.localStorage.removeItem(claimStorageKey(chain.chain_id, wallet)); });
    return () => { active = false; };
  }, [wallet, chain]);

  useEffect(() => {
    if (!claim || !["reserved", "submitted", "broadcast_unknown"].includes(claim.status)) return;
    let active = true;
    const interval = window.setInterval(() => {
      faucetRequest<Claim>("/claims/" + encodeURIComponent(claim.claim_id))
        .then((value) => { if (active) setClaim(value); })
        .catch(() => { });
    }, 4000);
    return () => { active = false; window.clearInterval(interval); };
  }, [claim]);

  const connect = useCallback(async (): Promise<string> => {
    if (!window.ethereum) throw new Error("Install an EVM wallet such as MetaMask to continue.");
    const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
    if (!Array.isArray(accounts) || typeof accounts[0] !== "string") throw new Error("No wallet account was selected.");
    setWallet(accounts[0]);
    return accounts[0];
  }, []);

  const changeWallet = async () => {
    if (busy) {
      setError("Finish or reject the current wallet prompt before changing accounts.");
      return;
    }
    if (!window.ethereum || changingWallet) return;
    setError("");
    setChangingWallet(true);
    try {
      await window.ethereum.request({ method: "wallet_requestPermissions", params: [{ eth_accounts: {} }] });
      const accounts = await window.ethereum.request({ method: "eth_accounts" });
      if (!Array.isArray(accounts) || typeof accounts[0] !== "string") {
        throw new Error("No wallet account was selected.");
      }
      setWallet(accounts[0]);
      setClaim(null);
    } catch (reason) {
      setError(walletError(reason));
    } finally {
      setChangingWallet(false);
    }
  };

  const switchChain = useCallback(async (selected: Chain) => {
    const provider = window.ethereum;
    if (!provider) throw new Error("No wallet provider found.");
    const hex = "0x" + selected.chain_id.toString(16);
    const current = await provider.request({ method: "eth_chainId" });
    if (current === hex) return;
    try {
      await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
    } catch (reason) {
      const code = (reason as { code?: number })?.code;
      if (code !== 4902) throw reason;
      const rpc = process.env.NEXT_PUBLIC_ROBURNA_RPC_URL ?? "https://preseed-testnet-1.roburna.com";
      if (selected.chain_id !== 159 && !process.env.NEXT_PUBLIC_ROBURNA_RPC_URL) {
        throw new Error("Add this network to your wallet before requesting tokens.");
      }
      await provider.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: hex,
          chainName: selected.name,
          nativeCurrency: { name: selected.currency_symbol, symbol: selected.currency_symbol, decimals: selected.currency_decimals },
          rpcUrls: [rpc],
          ...(selected.explorer_url ? { blockExplorerUrls: [selected.explorer_url] } : {}),
        }],
      });
    }
  }, []);

  const requestTokens = async () => {
    if (!chain || busy) return;
    setError("");
    setBusy(true);
    try {
      setStage("Connecting wallet…");
      const address = wallet || await connect();
      setStage("Checking network…");
      await switchChain(chain);
      setStage("Preparing sign-in message…");
      const challenge = await faucetRequest<Challenge>("/challenge", { wallet_address: address, chain_id: chain.chain_id });
      setStage("Waiting for wallet signature…");
      // personal_sign signs the exact SIWE text; it does not send a transaction or spend gas.
      const signature = await window.ethereum?.request({ method: "personal_sign", params: [messageToHex(challenge.message), address] });
      if (typeof signature !== "string") throw new Error("The wallet did not return a signature.");
      setStage("Submitting your claim…");
      const result = await faucetRequest<Claim>("/claims", {
        challenge_id: challenge.challenge_id,
        chain_id: chain.chain_id,
        wallet_address: address,
        message: challenge.message,
        signature,
      });
      window.localStorage.setItem(claimStorageKey(chain.chain_id, address), result.claim_id);
      setClaim(result);
    } catch (reason) {
      setError(walletError(reason));
    } finally {
      setBusy(false);
      setStage("");
    }
  };

  const active = claim && ["reserved", "submitted", "broadcast_unknown"].includes(claim.status);
  const cooldown = claim?.status === "confirmed" && claim.next_eligible_at && new Date(claim.next_eligible_at).getTime() > now;
  const delayed = claim?.status === "reserved" && now - new Date(claim.reserved_at).getTime() > 120_000;
  const amount = chain ? formatUnits(chain.payout_amount_wei, chain.currency_decimals) : "—";

  return (
    <section className="overflow-hidden rounded-[22px] border border-[#e3e9df] bg-white shadow-[0_25px_70px_#0a3d2a14]" aria-labelledby="faucet-heading">
      <div className="relative overflow-hidden bg-linear-to-br from-[#094e30] via-[#007a3e] to-[#13854a] px-5 pt-[31px] pb-[27px] text-white sm:px-9">
        <span className="text-[10px] font-extrabold tracking-[2px] text-[#c3e8c6]">ROBURNA / TESTNET</span>
        <h2 id="faucet-heading" className="mt-[15px] mb-[7px] text-[31px] tracking-[-1.3px]">Get test tokens</h2>
        <p className="text-[13px] leading-normal text-[#dbf1df]">A little fuel to get your next idea moving.</p>
      </div>
      <div className="px-5 pt-[30px] pb-8 sm:px-9">
        <span className="mb-[11px] block text-[11px] font-extrabold tracking-[1.5px] text-[#456854] uppercase">Network</span>
        <div className="mb-[25px] flex min-h-[58px] min-w-0 items-center gap-3 rounded-[10px] border border-[#dbe5da] bg-[#fbfcfa] px-4 py-3">

          <div><div className="text-sm font-extrabold">{chain?.name ?? (loading ? "Loading network…" : "No network available")}</div><div className="mt-[3px] text-[11px] text-[#81978a]">EVM compatible test network</div></div>
          <span className="ml-auto hidden whitespace-nowrap text-[11px] text-[#6d8978] sm:inline">{chain ? "CHAIN ID " + chain.chain_id : ""}</span>
        </div>
        <span className="mb-[11px] block text-[11px] font-extrabold tracking-[1.5px] text-[#456854] uppercase">Recipient wallet</span>
        <div className="mb-[13px] flex min-h-[58px] min-w-0 items-center justify-between gap-3 rounded-[10px] border border-[#dbe5da] bg-[#fbfcfa] px-4 py-3">

          <div className="min-w-0 flex-1"><div className="truncate text-sm font-extrabold">{wallet ? wallet.slice(0, 6) + "…" + wallet.slice(-4) : "No wallet connected"}</div><div className="mt-[3px] text-[11px] text-[#81978a]">{wallet ? "Connected wallet" : "Your EVM wallet address"}</div></div>
          <button className="min-h-10 shrink-0 cursor-pointer rounded-md px-2 text-xs font-extrabold text-[#007a3e] hover:bg-[#e6f3e7] disabled:cursor-not-allowed disabled:opacity-60" type="button" disabled={changingWallet} onClick={() => { if (wallet) { void changeWallet(); } else { connect().catch((reason) => setError(walletError(reason))); } }}>{changingWallet ? "Changing…" : wallet ? "Change" : "Connect"}</button>
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-[#edf0eb] pt-[17px] pb-5"><span className="text-xs text-[#6d8474]">Amount per request</span><strong className="text-[17px]">{amount} {chain?.currency_symbol ?? "RBAT"}</strong></div>
        {error && <p className="mb-[15px] rounded-[9px] border border-[#f2d6cd] bg-[#fff1ec] px-[15px] py-[13px] text-xs leading-normal break-words text-[#8c2e25]" role="alert">{error}</p>}
        {delayed && <p className="mb-[15px] rounded-[9px] border border-[#e4d7b8] bg-[#fff9ea] px-[15px] py-[13px] text-xs leading-normal text-[#75602b]" role="status">Processing is taking longer than expected. Keep this claim ID and check again shortly.</p>}
        {claim && !error && <p className={"mb-[15px] rounded-[9px] border px-[15px] py-[13px] text-xs leading-normal break-words " + (claim.status === "confirmed" ? "border-[#c7e7cd] bg-[#eaf7ec] text-[#215638]" : "border-[#d4e8d9] bg-[#eef6ef] text-[#355d48]")} role="status">{claimMessage(claim)}</p>}
        <button className="flex min-h-[55px] w-full cursor-pointer items-center justify-center gap-[10px] rounded-[10px] bg-[#007a3e] px-3 text-sm font-extrabold text-white transition duration-200 enabled:hover:-translate-y-px enabled:hover:bg-[#075f37] disabled:cursor-not-allowed disabled:opacity-60" type="button" disabled={loading || !chain || busy || Boolean(active) || Boolean(cooldown)} onClick={requestTokens}>
          {busy ? stage : active ? "Processing your claim…" : cooldown ? "Wallet is in cooldown" : wallet ? "Request test tokens" : "Connect wallet to request"}
        </button>
        <p className="mt-4 text-center text-[11px] leading-normal text-[#8a9b8f]">No gas required to claim. One successful claim every {chain ? Math.round(chain.cooldown_seconds / 3600) : 24} hours per wallet.</p>
        {claim && <dl className="mt-[15px] grid gap-2 border-t border-[#edf0eb] pt-[17px] text-xs [&_div]:flex [&_div]:justify-between [&_div]:gap-3 [&_dt]:text-[#839789] [&_dd]:m-0 [&_dd]:text-right [&_dd]:font-bold [&_dd]:break-words [&_a]:text-[#007a3e]">

          <div><dt>Status</dt><dd>{claim.status === "reserved" ? "processing" : claim.status === "broadcast_unknown" ? (claim.failure_code === "rpc_transaction_pool_unavailable" ? "RPC unavailable" : "processing") : claim.status.replace("_", " ")}</dd></div>
          {claim.transaction_hash && <div><dt>Transaction</dt><dd>{claim.transaction_url ? <a href={claim.transaction_url} target="_blank" rel="noreferrer">{claim.transaction_hash.slice(0, 10)}…</a> : claim.transaction_hash.slice(0, 10) + "........." +   claim.transaction_hash.slice(-4)}</dd></div>}
          {cooldown && <div><dt>Next claim</dt><dd>{new Date(claim.next_eligible_at!).toLocaleString()}</dd></div>}
        </dl>}
      </div>
    </section>
  );
}
