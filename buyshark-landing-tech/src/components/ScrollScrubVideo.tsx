import { useEffect, useRef, type ReactNode } from 'react';
import { gsap, ScrollTrigger } from '../lib/gsap';
import styles from './ScrollScrubVideo.module.css';

interface ScrollScrubVideoProps {
  /** mp4 source, scrubbed via ScrollTrigger */
  src: string;
  poster?: string;
  /** total pinned scroll distance, in multiples of viewport height */
  scrollMultiplier?: number;
  className?: string;
  id?: string;
  children?: ReactNode;
  onProgress?: (progress: number) => void;
}

export function ScrollScrubVideo({
  src,
  poster,
  scrollMultiplier = 2.5,
  className,
  id,
  children,
  onProgress,
}: ScrollScrubVideoProps) {
  const sectionRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const section = sectionRef.current;
    const video = videoRef.current;
    if (!section || !video) return;

    // Created synchronously (not gated on the video's `loadedmetadata`) so this pin's spacer
    // reserves its real height in the same pass as every other scene's trigger — gating on
    // metadata used to let later sections measure their start position against the
    // pre-pin layout (this section still at its natural, unpinned height) whenever a video
    // was slow to resolve, causing the next section to start pinning before this one's scrub
    // actually finished. `video.duration` is read fresh on every tick instead, guarded for the
    // NaN it holds before metadata loads.
    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top top',
      end: () => `+=${window.innerHeight * scrollMultiplier}`,
      pin: true,
      scrub: 0.5,
      invalidateOnRefresh: true,
      onUpdate: (self) => {
        const duration = video.duration;
        if (!Number.isNaN(duration)) {
          video.currentTime = self.progress * duration;
        }
        onProgress?.(self.progress);
      },
    });

    return () => {
      trigger.kill();
    };
  }, [scrollMultiplier, onProgress]);

  useEffect(() => {
    return () => {
      gsap.killTweensOf(videoRef.current);
    };
  }, []);

  return (
    <section ref={sectionRef} id={id} className={`${styles.section} ${className ?? ''}`}>
      <video
        ref={videoRef}
        className={styles.video}
        src={src}
        poster={poster}
        muted
        playsInline
        preload="auto"
        disablePictureInPicture
      />
      <div className={styles.overlay}>{children}</div>
    </section>
  );
}
