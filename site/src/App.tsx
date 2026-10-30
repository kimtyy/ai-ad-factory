import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Navbar } from './components/Navbar';
import { ScrambleIn } from './components/ScrambleText';
import { ConnectAILabLogo } from './components/ConnectAILabLogo';
import { SITE_CONFIG } from './config/content';
import { VIDEO_URLS } from './config/videos';

const SHADOW = { textShadow: '0 2px 8px rgba(0,0,0,0.8)' };
const reveal = {
  initial: { opacity: 0, y: 30 },
  whileInView: { opacity: 1, y: 0 },
  transition: { duration: 0.8 },
  viewport: { once: true, amount: 0.25 },
};

export default function App() {
  const [entranceComplete, setEntranceComplete] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setEntranceComplete(true), 800);
    return () => clearTimeout(timer);
  }, []);

  const { hero, sample, metrics, process, platforms, compliance, appUrl, supportEmail } = SITE_CONFIG;

  return (
    <div className="bg-black text-white min-h-screen">
      <Navbar entranceComplete={entranceComplete} />

      {/* ───── HERO ───── */}
      <section className="relative h-screen h-[100dvh] flex flex-col overflow-hidden">
        {VIDEO_URLS.hero && (
          <video
            src={VIDEO_URLS.hero}
            className="absolute inset-0 w-full h-full object-cover scale-110 blur-md"
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
          />
        )}
        <div className="absolute inset-0 bg-black/55 z-10" />
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10" style={{ paddingTop: 50 }}>
          <span
            className="uppercase select-none"
            style={{ fontFamily: '"Anton SC", sans-serif', fontSize: 'clamp(70px, 18vw, 320px)', letterSpacing: '-4px', opacity: 0.08, color: '#fff', lineHeight: 1 }}
          >
            {hero.watermark}
          </span>
        </div>

        <motion.div
          className="relative z-20 flex flex-col flex-1 px-4 sm:px-6 md:px-8 pt-24 pb-10"
          initial={{ opacity: 0 }}
          animate={{ opacity: entranceComplete ? 1 : 0 }}
          transition={{ duration: 1 }}
        >
          <div className="flex-1" />
          <p className="text-[13px] sm:text-[15px] text-white/90 font-semibold mb-4" style={SHADOW}>{hero.badge}</p>
          <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <div className="flex flex-col gap-4 items-start">
              <h1 className="text-white font-light leading-[1.02] tracking-[-0.03em]" style={{ fontSize: 'clamp(36px, 7.5vw, 80px)', ...SHADOW }}>
                <ScrambleIn text={hero.titleLeft[0]} delay={200} triggered={entranceComplete} />
                <br />
                <ScrambleIn text={hero.titleLeft[1]} delay={500} triggered={entranceComplete} />
              </h1>
              <p className="max-w-md text-[13px] sm:text-[15px] text-white/90 font-medium leading-relaxed" style={SHADOW}>
                {hero.description}
              </p>
              <div className="flex flex-wrap gap-3 mt-4">
                <a
                  href={appUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-7 py-3.5 bg-[#ff5a36] hover:bg-[#e84a28] text-white font-semibold rounded-full text-[14px] sm:text-[15px] no-underline active:scale-95 transition-all"
                >
                  {hero.ctaButton}
                </a>
                <a
                  href="#sample"
                  className="px-7 py-3.5 bg-white/15 hover:bg-white/25 backdrop-blur-md text-white font-semibold rounded-full text-[14px] sm:text-[15px] no-underline active:scale-95 transition-all"
                >
                  {hero.secondaryButton}
                </a>
              </div>
            </div>
            <h1 className="text-white font-light leading-[1.02] tracking-[-0.03em] text-left md:text-right" style={{ fontSize: 'clamp(36px, 7.5vw, 80px)', ...SHADOW }}>
              <ScrambleIn text={hero.titleRight[0]} delay={700} triggered={entranceComplete} />
              <br />
              <ScrambleIn text={hero.titleRight[1]} delay={1000} triggered={entranceComplete} />
            </h1>
          </div>
        </motion.div>
      </section>

      {/* ───── SAMPLE ───── */}
      <section id="sample" className="bg-zinc-950 py-24 px-6 scroll-mt-4">
        <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          <motion.div {...reveal}>
            <p className="text-white/60 text-[13px] tracking-[0.2em] uppercase mb-5 font-semibold">{sample.badge}</p>
            <h2 className="font-semibold leading-[1.15] tracking-[-0.02em] mb-6" style={{ fontSize: 'clamp(28px, 5vw, 48px)' }}>{sample.title}</h2>
            <p className="text-white/80 text-[15px] sm:text-[17px] leading-relaxed max-w-md">{sample.description}</p>
          </motion.div>
          <motion.div className="flex justify-center" {...reveal}>
            <div className="relative w-[250px] sm:w-[280px] aspect-[9/19] bg-zinc-900 rounded-[44px] p-3 border-4 border-zinc-700 shadow-2xl overflow-hidden shrink-0">
              <div className="absolute top-4 left-1/2 -translate-x-1/2 w-24 h-4 bg-zinc-800 rounded-full z-30" />
              <div className="w-full h-full rounded-[32px] overflow-hidden bg-black">
                {VIDEO_URLS.sample && (
                  <video
                    src={VIDEO_URLS.sample}
                    className="w-full h-full object-cover"
                    controls
                    muted
                    loop
                    playsInline
                    preload="none"
                  />
                )}
              </div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* ───── METRICS ───── */}
      <section className="bg-black py-24 px-6 border-t border-white/10">
        <div className="max-w-6xl mx-auto">
          <motion.p className="text-white/70 text-[13px] sm:text-[14px] tracking-[0.2em] mb-16 text-center font-semibold" {...reveal}>
            {metrics.title}
          </motion.p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-14 text-center">
            {metrics.items.map((m, i) => (
              <motion.div key={m.label} {...reveal} transition={{ duration: 0.8, delay: i * 0.12 }}>
                <div className="font-semibold tracking-[-0.04em] leading-none" style={{ fontSize: 'clamp(32px, 5vw, 56px)' }}>{m.value}</div>
                <div className="text-white/80 text-[13px] sm:text-[15px] mt-4 font-medium">{m.label}</div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── PROCESS ───── */}
      <section className="bg-white text-zinc-900 py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <motion.div className="text-center mb-16" {...reveal}>
            <p className="text-zinc-500 text-[13px] tracking-[0.2em] mb-5 font-semibold">{process.badge}</p>
            <h2 className="font-semibold leading-[1.2] tracking-[-0.02em]" style={{ fontSize: 'clamp(24px, 4.5vw, 42px)' }}>{process.title}</h2>
          </motion.div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {process.steps.map((s, i) => (
              <motion.div key={s.step} className="border border-zinc-200 rounded-2xl p-8 shadow-sm flex flex-col gap-3" {...reveal} transition={{ duration: 0.7, delay: i * 0.1 }}>
                <span className="text-[#ff5a36] text-[15px] font-bold">STEP {s.step}</span>
                <h3 className="text-[18px] sm:text-[20px] font-bold tracking-tight">{s.title}</h3>
                <p className="text-zinc-600 text-[14px] sm:text-[15px] leading-relaxed">{s.description}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── PLATFORMS ───── */}
      <section className="bg-zinc-950 py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <motion.h2 className="font-semibold leading-[1.2] tracking-[-0.02em] text-center mb-14" style={{ fontSize: 'clamp(24px, 4.5vw, 42px)' }} {...reveal}>
            {platforms.title}
          </motion.h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            {platforms.items.map((p, i) => (
              <motion.div key={p.name} className="border border-white/15 bg-white/5 rounded-2xl p-7 flex flex-col gap-3" {...reveal} transition={{ duration: 0.7, delay: i * 0.08 }}>
                <h3 className="text-[18px] font-bold">{p.name}</h3>
                <p className="text-white/75 text-[14px] leading-relaxed">{p.description}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── COMPLIANCE ───── */}
      <section className="bg-black py-20 px-6 border-t border-white/10">
        <div className="max-w-3xl mx-auto">
          <motion.h2 className="font-semibold text-center mb-10" style={{ fontSize: 'clamp(22px, 4vw, 34px)' }} {...reveal}>
            {compliance.title}
          </motion.h2>
          <motion.div className="border border-white/15 rounded-2xl p-7" {...reveal}>
            <h3 className="text-[17px] font-bold mb-3">{compliance.youtubeApiNotice.title}</h3>
            <p className="text-white/75 text-[14px] sm:text-[15px] leading-relaxed">{compliance.youtubeApiNotice.content}</p>
          </motion.div>
          <p className="text-white/60 text-[14px] text-center mt-8">{compliance.pinNotice}</p>
          <div className="text-center mt-6">
            <a
              href={appUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block px-8 py-4 bg-[#ff5a36] hover:bg-[#e84a28] text-white font-semibold rounded-full text-[15px] no-underline active:scale-95 transition-all"
            >
              {hero.ctaButton}
            </a>
          </div>
        </div>
      </section>

      {/* ───── FOOTER ───── */}
      <footer className="bg-black border-t border-white/10 px-6 py-12">
        <div className="max-w-6xl mx-auto flex flex-col gap-6">
          <div className="flex items-center gap-2.5">
            <ConnectAILabLogo size={18} className="text-white/80" />
            <span className="text-[16px] font-medium text-white/85">{SITE_CONFIG.brandName}</span>
          </div>
          <p className="text-white/70 text-[14px]">{SITE_CONFIG.tagline}</p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
            <a href={compliance.privacyPolicyLink} className="text-white/85 underline">개인정보처리방침</a>
            <a href={`mailto:${supportEmail}`} className="text-white/85 underline">문의: {supportEmail}</a>
          </div>
          <div className="text-white/50 text-[12px] leading-relaxed">
            {SITE_CONFIG.company.lines.map((l) => (
              <p key={l}>{l}</p>
            ))}
          </div>
          <p className="text-white/40 text-[12px]">{SITE_CONFIG.copyright}</p>
        </div>
      </footer>
    </div>
  );
}
