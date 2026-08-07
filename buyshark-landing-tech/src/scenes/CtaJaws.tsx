import { useRef } from 'react';
import { gsap } from '../lib/gsap';
import { ScrollScrubVideo } from '../components/ScrollScrubVideo';
import styles from './CtaJaws.module.css';

let audioCtx: AudioContext | null = null;

// synthesized in-browser — no recorded SFX asset needed, so this costs nothing to ship or regenerate
function playBassDrop() {
  try {
    audioCtx ??= new AudioContext();
    const ctx = audioCtx;
    if (ctx.state === 'suspended') ctx.resume();
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    const oscGain = ctx.createGain();
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(190, now);
    osc.frequency.exponentialRampToValueAtTime(36, now + 0.35);
    oscGain.gain.setValueAtTime(0.0001, now);
    oscGain.gain.exponentialRampToValueAtTime(0.5, now + 0.03);
    oscGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.45);
    osc.connect(oscGain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.5);

    // low growl texture riding under the drop, so it reads as a "roar" rather than a pure sine thud
    const bufferSize = Math.floor(ctx.sampleRate * 0.4);
    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / bufferSize);
    const noise = ctx.createBufferSource();
    noise.buffer = buffer;
    const noiseFilter = ctx.createBiquadFilter();
    noiseFilter.type = 'lowpass';
    noiseFilter.frequency.setValueAtTime(320, now);
    const noiseGain = ctx.createGain();
    noiseGain.gain.setValueAtTime(0.28, now);
    noiseGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.4);
    noise.connect(noiseFilter).connect(noiseGain).connect(ctx.destination);
    noise.start(now);
  } catch {
    // blocked by autoplay policy in some environments — the visual shake/flash still lands
  }
}

/** Scene 6 — BuyShark's jaws open on the viewer and the camera flies inside, scroll-scrubbed; the signup panel lives inside the mouth. */
const LOOP_IN_AT = 0.985; // scrub progress past which the frozen last frame hands off to the ambient loop

export function CtaJaws() {
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const flashRef = useRef<HTMLDivElement>(null);
  const loopRef = useRef<HTMLVideoElement>(null);

  const handleProgress = (progress: number) => {
    const panel = panelRef.current;
    if (panel) {
      const t = Math.min(1, Math.max(0, (progress - 0.7) / 0.3));
      panel.style.opacity = String(t);
      panel.style.transform = `translateY(${(1 - t) * 30}px) scale(${0.94 + t * 0.06})`;
    }
    const loop = loopRef.current;
    if (loop) {
      loop.style.opacity = progress >= LOOP_IN_AT ? '1' : '0';
    }
  };

  const handleStart = () => {
    window.location.href = 'https://app.buyshark.kz/';
  };

  const handleHover = () => {
    const btn = buttonRef.current;
    if (btn) {
      btn.classList.remove(styles.shake);
      void btn.offsetWidth; // restart the shake animation on repeated hovers
      btn.classList.add(styles.shake);
    }
    const flash = flashRef.current;
    if (flash) {
      gsap.killTweensOf(flash);
      gsap.set(flash, { opacity: 0.9 });
      gsap.to(flash, { opacity: 0, duration: 0.5, ease: 'power2.out' });
    }
    playBassDrop();
  };

  return (
    <ScrollScrubVideo
      id="cta"
      src="/media/scene6-bg.mp4"
      scrollMultiplier={2.2}
      onProgress={handleProgress}
      className={styles.section}
    >
      <video
        ref={loopRef}
        className={styles.loopVideo}
        src="/media/scene6-loop.mp4"
        autoPlay
        loop
        muted
        playsInline
        style={{ opacity: 0 }}
      />
      <div className={styles.vignette} />
      <div ref={flashRef} className={styles.flash} style={{ opacity: 0 }} />

      <div ref={panelRef} className={styles.panel} style={{ opacity: 0 }}>
        <h2 className={styles.eyebrow}>Стань хищником, а не добычей</h2>
        <input className={styles.input} type="text" placeholder="Введите название вашего магазина" />
        <button ref={buttonRef} className={styles.cta} onMouseEnter={handleHover} onClick={handleStart}>
          Попробовать 3 дня бесплатно
        </button>
      </div>
    </ScrollScrubVideo>
  );
}
