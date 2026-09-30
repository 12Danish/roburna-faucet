import Image from "next/image";
import FaucetPanel from "@/components/faucet-panel";

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col bg-[#f6f5ed] bg-[radial-gradient(circle_at_77%_8%,#e5f1da_0,transparent_30%)] font-sans text-[#103e2c]">
      <header className="mx-auto flex min-h-[152px] w-[calc(100%-32px)] max-w-[1320px] items-center justify-start border-b border-[#d7dfd2] sm:min-h-[184px] sm:w-[calc(100%-64px)] lg:w-[calc(100%-96px)]">
        <a href="https://roburnalabs.com/" target="_blank" rel="noreferrer" aria-label="Roburna Labs website" className="grid size-36 place-items-center sm:size-44">
          <Image src="/roburna-logo-green.svg" alt="Roburna Labs" width={176} height={176} priority className="size-full object-contain" />
        </a>
      </header>

      <main className="mx-auto my-auto grid w-[calc(100%-32px)] max-w-[1320px] grid-cols-1 items-center gap-10 py-10 sm:w-[calc(100%-64px)] lg:w-[calc(100%-96px)] sm:py-14 lg:grid-cols-[minmax(0,1.1fr)_minmax(350px,.9fr)] lg:gap-[clamp(48px,8vw,150px)] lg:pt-[74px] lg:pb-24 max-lg:max-w-[620px]">
        <section className="max-w-[650px]" aria-labelledby="page-title">
          <div className="flex items-center gap-[14px] text-[11px] font-extrabold tracking-[2.7px] text-[#007a3e]">
            <span className="h-px w-[26px] bg-[#007a3e]" /> BUILT FOR BUILDERS <span className="h-px w-[26px] bg-[#007a3e]" />
          </div>
          <h1 id="page-title" className="mt-7 mb-[26px] text-[clamp(38px,10.5vw,64px)] leading-[1.02] font-extrabold tracking-[-.074em] sm:text-[clamp(58px,7vw,106px)]">Fuel your<br /><em className="not-italic text-[#007a3e]">next build.</em></h1>
          <p className="max-w-[485px] text-base leading-[1.65] text-[#60786a] sm:text-lg">Get Roburna testnet RBAT for deploying contracts, testing transactions, and bringing your ideas onchain.</p>
          <div className="mt-[35px] grid gap-[17px] border-t border-[#d7dfd2] pt-[27px] lg:mt-[65px]">
            <div className="flex items-baseline gap-[19px] text-sm text-[#516c5d]"><span className="text-xs font-extrabold tracking-[1px] text-[#007a3e]">01</span><span>Connect your wallet</span></div>
            <div className="flex items-baseline gap-[19px] text-sm text-[#516c5d]"><span className="text-xs font-extrabold tracking-[1px] text-[#007a3e]">02</span><span>Sign a message to prove it is yours</span></div>
            <div className="flex items-baseline gap-[19px] text-sm text-[#516c5d]"><span className="text-xs font-extrabold tracking-[1px] text-[#007a3e]">03</span><span>Receive testnet RBAT</span></div>
          </div>
        </section>
        <FaucetPanel />
      </main>

    </div>
  );
}
