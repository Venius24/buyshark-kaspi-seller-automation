import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../index.css';
import { HelpApp } from './HelpApp';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HelpApp />
  </StrictMode>,
);
