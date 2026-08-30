import { Nav } from './components/Nav';
import { Footer } from './components/Footer';
import { FloatingContact } from './components/FloatingContact';
import { Hero } from './scenes/Hero';
import { Problem } from './scenes/Problem';
import { Features } from './scenes/Features';
import { PricingReef } from './scenes/PricingReef';
import { FinalCta } from './scenes/FinalCta';

function App() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Problem />
        <Features />
        <PricingReef />
        <FinalCta />
      </main>
      <Footer />
      <FloatingContact />
    </>
  );
}

export default App;
