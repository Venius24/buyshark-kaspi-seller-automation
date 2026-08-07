import { useEffect } from 'react';
import { ScrollTrigger } from './lib/gsap';
import { Hero } from './scenes/Hero';
import { SlotMachine } from './scenes/SlotMachine';
import { PriceBiteGame } from './scenes/PriceBiteGame';
import { UnderwaterRadar } from './scenes/UnderwaterRadar';
import { PricingReef } from './scenes/PricingReef';
import { CtaJaws } from './scenes/CtaJaws';

function App() {
  useEffect(() => {
    // pinned scroll-scrub sections measure layout at creation time; late-settling
    // web fonts and video metadata can shift heights afterwards, so re-measure once
    // everything has actually loaded to avoid drifting pin/spacer lengths.
    const refresh = () => ScrollTrigger.refresh();
    document.fonts?.ready.then(refresh);
    if (document.readyState === 'complete') {
      refresh();
    } else {
      window.addEventListener('load', refresh);
    }
    return () => window.removeEventListener('load', refresh);
  }, []);

  return (
    <main>
      <Hero />
      <SlotMachine />
      <PriceBiteGame />
      <UnderwaterRadar />
      <PricingReef />
      <CtaJaws />
    </main>
  );
}

export default App;
