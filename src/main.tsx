import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import PublicPortfolio from './public/PublicPortfolio';
import './index.css';

const root = createRoot(document.getElementById('root')!);
const hostname = window.location.hostname.toLowerCase();
const isPrivateEditor = hostname === 'localhost' || hostname === '127.0.0.1'
  || hostname === '::1' || hostname.endsWith('.ts.net');
const isPublicDemo = !isPrivateEditor && new URLSearchParams(window.location.search).get('demo') === '1';

if (isPrivateEditor || isPublicDemo) {
  void Promise.all([
    import('./locales'),
    import('./App'),
    import('./auth/AuthGate'),
    import('./auth/client'),
    import('./auth/auth.css'),
  ]).then(([localeModule, appModule, authModule, clientModule]) => {
    clientModule.installAuthFetch();
    const LanguageProvider = localeModule.LanguageProvider;
    const App = appModule.default;
    const AuthGate = authModule.AuthGate;
    root.render(
      <StrictMode>
        <LanguageProvider>
          <AuthGate initialGuestPreview={isPublicDemo}><App /></AuthGate>
        </LanguageProvider>
      </StrictMode>,
    );
  }).catch(() => {
    root.render(<main className="portfolio-load-error">{isPublicDemo
      ? 'The Void Cut demo could not be loaded. Refresh and try again.'
      : 'The private editor could not be loaded. Check the laptop connection and try again.'}</main>);
  });
} else {
  root.render(<StrictMode><PublicPortfolio /></StrictMode>);
}
